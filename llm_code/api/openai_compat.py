"""OpenAI-compatible provider implementation."""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

import httpx

from llm_code.api.errors import (
    ProviderAuthError,
    ProviderConnectionError,
    ProviderModelNotFoundError,
    ProviderOverloadError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from llm_code.api.provider import LLMProvider
from llm_code.api.sse import parse_sse_events
from llm_code.api.types import (
    ContentBlock,
    ImageBlock,
    Message,
    MessageRequest,
    MessageResponse,
    StreamEvent,
    StreamMessageStop,
    StreamTextDelta,
    StreamToolUseInputDelta,
    StreamToolUseStart,
    TextBlock,
    TokenUsage,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)


# Wave2-1b: hard cap on provider-reported Retry-After so a misbehaving
# proxy that returns "Retry-After: 86400" does not wedge the runtime
# for a day. Real providers use small values (30s typical on 429).
_MAX_RETRY_AFTER_SECONDS = 60.0


def _parse_retry_after_header(raw: str | None) -> float | None:
    """Parse an HTTP Retry-After value into a seconds float.

    Returns None on missing, empty, or unparseable input. Negative
    or absurdly large values are clamped to the max cap. The HTTP
    spec allows either a delta-seconds integer or an HTTP-date;
    this helper only handles the delta-seconds form which is what
    every real LLM provider actually sends on 429.
    """
    if not raw:
        return None
    try:
        value = float(raw.strip())
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return min(value, _MAX_RETRY_AFTER_SECONDS)


def _token_usage_from_dict(usage_data: dict) -> "TokenUsage":
    """Build TokenUsage from a raw provider usage dict (wave2-2).

    Handles both payload shapes:

    * OpenAI-compat (``prompt_tokens`` / ``completion_tokens`` +
      ``prompt_tokens_details.cached_tokens`` for cache reads).
    * Anthropic-style (``cache_read_input_tokens`` /
      ``cache_creation_input_tokens`` top-level).

    Falls back to 0 for any missing field so upstream code can assume
    a fully-populated object.
    """
    input_tokens = int(usage_data.get("prompt_tokens") or usage_data.get("input_tokens") or 0)
    output_tokens = int(usage_data.get("completion_tokens") or usage_data.get("output_tokens") or 0)

    cache_read = 0
    details = usage_data.get("prompt_tokens_details")
    if isinstance(details, dict):
        cache_read = int(details.get("cached_tokens") or 0)
    cache_read = int(usage_data.get("cache_read_input_tokens") or cache_read)
    cache_creation = int(usage_data.get("cache_creation_input_tokens") or 0)

    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_creation_tokens=cache_creation,
    )


class OpenAICompatProvider(LLMProvider):
    """Provider adapter for OpenAI-compatible APIs (Ollama, vLLM, LM Studio, etc.)."""

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        model_name: str = "",
        max_retries: int = 2,
        timeout: float = 120.0,
        native_tools: bool = True,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model_name = model_name
        self._max_retries = max_retries
        self._timeout = timeout
        self._native_tools = native_tools

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        self._client = httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(timeout),
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def send_message(self, request: MessageRequest) -> MessageResponse:
        payload = self._build_payload(request, stream=False)
        response = await self._post_with_retry(payload)
        return self._parse_response(response)

    async def stream_message(self, request: MessageRequest) -> AsyncIterator[StreamEvent]:
        payload = self._build_payload(request, stream=True)
        response = await self._post_with_retry(payload)
        return self._iter_stream_events(response.text)

    def supports_native_tools(self) -> bool:
        return self._native_tools

    def supports_images(self) -> bool:
        return False

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_messages(
        self,
        messages: tuple[Message, ...],
        system: str | None = None,
    ) -> list[dict]:
        result: list[dict] = []

        if system:
            result.append({"role": "system", "content": system})

        for msg in messages:
            result.append(self._convert_message(msg))

        return result

    def _convert_message(self, msg: Message) -> dict:
        # Tool result messages use the "tool" role in OpenAI format
        if msg.role == "tool" or (
            len(msg.content) == 1 and isinstance(msg.content[0], ToolResultBlock)
        ):
            block = msg.content[0]
            assert isinstance(block, ToolResultBlock)
            return {
                "role": "tool",
                "tool_call_id": block.tool_use_id,
                "content": block.content,
            }

        # Check if content is mixed (has images or multiple block types)
        has_image = any(isinstance(b, ImageBlock) for b in msg.content)
        has_multiple = len(msg.content) > 1

        if has_image or has_multiple:
            parts: list[dict] = []
            for block in msg.content:
                if isinstance(block, TextBlock):
                    parts.append({"type": "text", "text": block.text})
                elif isinstance(block, ImageBlock):
                    parts.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{block.media_type};base64,{block.data}"
                        },
                    })
            return {"role": msg.role, "content": parts}

        # Single text block — use string content for simplicity
        if len(msg.content) == 1 and isinstance(msg.content[0], TextBlock):
            return {"role": msg.role, "content": msg.content[0].text}

        # Fallback: concatenate text blocks
        text = "".join(
            b.text for b in msg.content if isinstance(b, TextBlock)
        )
        return {"role": msg.role, "content": text}

    def _build_payload(self, request: MessageRequest, *, stream: bool) -> dict:
        payload: dict = {
            "model": request.model or self._model_name,
            "messages": self._build_messages(request.messages, system=request.system),
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "stream": stream,
        }

        if request.tools and self._native_tools:
            payload["tools"] = [
                self._convert_tool(t) for t in request.tools
            ]

        if stream:
            payload["stream_options"] = {"include_usage": True}

        if request.extra_body:
            payload.update(request.extra_body)

        return payload

    def _convert_tool(self, tool: ToolDefinition) -> dict:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        }

    async def _post_with_retry(self, payload: dict) -> httpx.Response:
        url = f"{self._base_url}/chat/completions"
        last_exc: Exception | None = None

        # 529 Overload: separate long-backoff retry track (30s -> 60s -> 120s, max 3 attempts)
        _OVERLOAD_BACKOFFS = [30, 60, 120]
        _overload_attempt = 0
        attempt = 0

        while attempt <= self._max_retries:
            try:
                response = await self._client.post(url, json=payload)
                self._raise_for_status(response)
                return response
            except ProviderOverloadError as exc:
                last_exc = exc
                if _overload_attempt < len(_OVERLOAD_BACKOFFS):
                    backoff = _OVERLOAD_BACKOFFS[_overload_attempt]
                    _overload_attempt += 1
                    await asyncio.sleep(backoff)
                    # Overload retries don't count against normal retry budget
                    continue
                raise
            except ProviderRateLimitError as exc:
                # Wave2-1b: honor Retry-After hint from the provider
                # when set; otherwise fall back to exponential. This
                # is what avoids hammering a rate-limited provider
                # before its reset window expires.
                last_exc = exc
                if attempt < self._max_retries:
                    backoff = exc.retry_after if exc.retry_after is not None else float(2 ** attempt)
                    await asyncio.sleep(backoff)
                    attempt += 1
                    continue
                raise
            except ProviderConnectionError as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    await asyncio.sleep(2 ** attempt)
                    attempt += 1
                    continue
                raise
            except (ProviderAuthError, ProviderModelNotFoundError):
                raise
            except httpx.ConnectError as exc:
                last_exc = ProviderConnectionError(str(exc))
                if attempt < self._max_retries:
                    await asyncio.sleep(2 ** attempt)
                    attempt += 1
                    continue
                raise last_exc from exc
            except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as exc:
                # Wave2-1b: any httpx timeout flavor is now a distinct
                # ProviderTimeoutError (retryable) instead of falling
                # through to a generic Exception in conversation.py
                # that skipped the retry budget entirely.
                last_exc = ProviderTimeoutError(str(exc) or type(exc).__name__)
                if attempt < self._max_retries:
                    await asyncio.sleep(2 ** attempt)
                    attempt += 1
                    continue
                raise last_exc from exc
            attempt += 1

        raise last_exc  # type: ignore[misc]

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code == 200:
            return
        try:
            body = response.json()
            msg = body.get("error", {}).get("message", response.text)
        except Exception:
            msg = response.text

        if response.status_code == 401:
            raise ProviderAuthError(msg)
        if response.status_code == 404:
            raise ProviderModelNotFoundError(msg)
        if response.status_code == 429:
            # Wave2-1b: honor the provider's Retry-After hint when
            # present so the backoff respects its own rate-limit
            # reset window instead of guessing with 2**attempt.
            retry_after = _parse_retry_after_header(
                response.headers.get("Retry-After")
            )
            raise ProviderRateLimitError(msg, retry_after=retry_after)
        if response.status_code == 529:
            raise ProviderOverloadError(msg)
        if response.status_code >= 500:
            raise ProviderConnectionError(msg)
        # Other 4xx — treat as connection error
        raise ProviderConnectionError(f"HTTP {response.status_code}: {msg}")

    def _parse_response(self, response: httpx.Response) -> MessageResponse:
        data = response.json()
        choices = data.get("choices")
        if not choices:
            raise ProviderConnectionError(f"No choices in API response: {str(data)[:200]}")
        choice = choices[0]
        message = choice.get("message")
        if not message:
            raise ProviderConnectionError(f"No message in API choice: {str(choice)[:200]}")
        finish_reason = choice.get("finish_reason") or "stop"

        content_blocks: list[ContentBlock] = []

        tool_calls = message.get("tool_calls")
        if tool_calls:
            for tc in tool_calls:
                fn = tc["function"]
                try:
                    args = json.loads(fn["arguments"])
                except (json.JSONDecodeError, KeyError):
                    args = {}
                content_blocks.append(
                    ToolUseBlock(id=tc["id"], name=fn["name"], input=args)
                )
        else:
            text = message.get("content") or ""
            content_blocks.append(TextBlock(text=text))

        usage_data = data.get("usage", {})
        usage = _token_usage_from_dict(usage_data)

        return MessageResponse(
            content=tuple(content_blocks),
            usage=usage,
            stop_reason=finish_reason,
        )

    def _iter_stream_events(self, raw: str) -> _StreamIterator:
        """Return async iterator over parsed SSE stream events."""
        return _StreamIterator(raw)


class _StreamIterator:
    """Async iterator that wraps synchronous SSE parsing."""

    def __init__(self, raw: str) -> None:
        self._events = list(parse_sse_events(raw))
        self._index = 0
        self._pending_tool_calls: dict[int, dict] = {}
        self._processed: list[StreamEvent] = []
        self._done = False
        self._build_events()

    def _build_events(self) -> None:
        events: list[StreamEvent] = []
        pending_tools: dict[int, dict] = {}
        _stop_emitted = False
        _last_usage: dict = {}

        for chunk in self._events:
            # Some providers (vLLM, Ollama) send usage in a standalone
            # final chunk with no choices.  Capture it regardless.
            chunk_usage = chunk.get("usage")
            if chunk_usage:
                _last_usage = chunk_usage

            choices = chunk.get("choices", [])
            for choice in choices:
                delta = choice.get("delta", {})
                finish_reason = choice.get("finish_reason")

                # Text content delta
                text = delta.get("content")
                if text:
                    events.append(StreamTextDelta(text=text))

                # Tool call deltas
                tool_calls = delta.get("tool_calls", [])
                for tc in tool_calls:
                    idx = tc.get("index", 0)
                    if idx not in pending_tools:
                        pending_tools[idx] = {
                            "id": tc.get("id", ""),
                            "name": tc.get("function", {}).get("name", ""),
                            "args": "",
                        }
                        if pending_tools[idx]["name"]:
                            events.append(
                                StreamToolUseStart(
                                    id=pending_tools[idx]["id"],
                                    name=pending_tools[idx]["name"],
                                )
                            )
                    # Accumulate argument fragments
                    args_fragment = tc.get("function", {}).get("arguments", "")
                    if args_fragment:
                        pending_tools[idx]["args"] += args_fragment
                        events.append(
                            StreamToolUseInputDelta(
                                id=pending_tools[idx]["id"],
                                partial_json=args_fragment,
                            )
                        )

                # Stop event — emitted exactly once at the end
                if finish_reason and not _stop_emitted:
                    _stop_emitted = True
                    usage_data = chunk_usage or _last_usage or {}
                    usage = _token_usage_from_dict(usage_data)
                    events.append(
                        StreamMessageStop(usage=usage, stop_reason=finish_reason)
                    )

        # If usage arrived in a trailing chunk after finish_reason, patch it
        if _stop_emitted and _last_usage:
            for i in range(len(events) - 1, -1, -1):
                if isinstance(events[i], StreamMessageStop):
                    existing = events[i]
                    if existing.usage.input_tokens == 0 and existing.usage.output_tokens == 0:
                        events[i] = StreamMessageStop(
                            usage=_token_usage_from_dict(_last_usage),
                            stop_reason=existing.stop_reason,
                        )
                    break

        self._processed = events

    def __aiter__(self):
        return self

    async def __anext__(self) -> StreamEvent:
        if self._index >= len(self._processed):
            raise StopAsyncIteration
        event = self._processed[self._index]
        self._index += 1
        return event
