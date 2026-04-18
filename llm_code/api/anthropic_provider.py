"""Native Anthropic Messages API provider.

Uses httpx directly (no ``anthropic`` SDK dependency) against
``https://api.anthropic.com/v1/messages``. Supports:

* Extended thinking with signed ``ThinkingBlock`` round-trip
* Native tool use (function calling)
* Streaming SSE with Anthropic's event types
* Retry with Retry-After and overload backoff
* Image inputs (base64)
* Prompt caching via ``cache_control`` on system/message blocks
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
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
from llm_code.api.types import (
    ContentBlock,
    ImageBlock,
    Message,
    MessageRequest,
    MessageResponse,
    ServerToolResultBlock,
    ServerToolUseBlock,
    StreamEvent,
    StreamMessageStop,
    StreamServerToolBlock,
    StreamTextDelta,
    StreamThinkingDelta,
    StreamThinkingSignature,
    StreamToolUseInputDelta,
    StreamToolUseStart,
    TextBlock,
    ThinkingBlock,
    TokenUsage,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)

_logger = logging.getLogger(__name__)

_API_VERSION = "2023-06-01"
_DEFAULT_BASE_URL = "https://api.anthropic.com"
_MAX_RETRY_AFTER_SECONDS = 60.0
_BLOCK_SEPARATOR = re.compile(r"\r?\n\r?\n")


def _parse_retry_after(raw: str | None) -> float | None:
    if not raw:
        return None
    try:
        value = float(raw.strip())
    except (TypeError, ValueError):
        return None
    return min(value, _MAX_RETRY_AFTER_SECONDS) if value > 0 else None


class AnthropicProvider(LLMProvider):
    """Native Anthropic Messages API provider via httpx."""

    def __init__(
        self,
        api_key: str,
        model_name: str = "",
        timeout: float = 120.0,
        max_retries: int = 2,
        base_url: str = _DEFAULT_BASE_URL,
        rate_handler: "Any | None" = None,
    ) -> None:
        self._api_key = api_key
        self._model_name = model_name
        self._max_retries = max_retries
        self._timeout = timeout
        self._base_url = base_url.rstrip("/")
        self._prev_cache_read_tokens: int = 0
        # C3b opt-in: when set, _post_with_retry routes through the
        # shared rate_limiter.run_with_rate_limit loop instead of the
        # legacy path below.
        self._rate_handler = rate_handler

        self._client = httpx.AsyncClient(
            headers={
                "x-api-key": api_key,
                "anthropic-version": _API_VERSION,
                "anthropic-beta": "prompt-caching-2024-07-31",
                "content-type": "application/json",
            },
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
        return _AnthropicLiveStreamIterator(self._client, self._base_url, payload, self._max_retries)

    def supports_native_tools(self) -> bool:
        return True

    def supports_images(self) -> bool:
        return True

    def supports_reasoning(self) -> bool:
        return True

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Payload building
    # ------------------------------------------------------------------

    def _build_payload(self, request: MessageRequest, *, stream: bool) -> dict:
        payload: dict = {
            "model": request.model or self._model_name,
            "max_tokens": request.max_tokens,
            "messages": self._build_messages(request.messages),
        }

        if request.system:
            # Prompt caching: wrap system prompt as a content block array
            # with cache_control on the last block so the system prompt
            # is cached across turns.
            payload["system"] = [
                {
                    "type": "text",
                    "text": request.system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]

        if request.tools:
            tools = [self._convert_tool(t) for t in request.tools]
            # Prompt caching: mark the last tool definition as a cache
            # breakpoint so the full tool schema is cached.
            if tools:
                tools[-1]["cache_control"] = {"type": "ephemeral"}
            payload["tools"] = tools

        if stream:
            payload["stream"] = True

        # Extended thinking: pass budget via extra_body or detect config.
        # Two formats are supported depending on model profile:
        # - anthropic_native: {"thinking": {"type": "enabled", "budget_tokens": N}}
        # - chat_template_kwargs: {"chat_template_kwargs": {"enable_thinking": true, ...}}
        if request.extra_body:
            # Native Anthropic format — pass through directly
            if "thinking" in request.extra_body:
                payload["thinking"] = request.extra_body["thinking"]
            else:
                # Legacy chat_template_kwargs format — convert
                thinking_cfg = request.extra_body.get("chat_template_kwargs", {})
                if thinking_cfg.get("enable_thinking"):
                    budget = thinking_cfg.get("thinking_budget", 10000)
                    payload["thinking"] = {
                        "type": "enabled",
                        "budget_tokens": budget,
                    }
            # Pass through any other extra_body keys
            for k, v in request.extra_body.items():
                if k not in ("chat_template_kwargs", "thinking"):
                    payload[k] = v

        if request.temperature is not None and "thinking" not in payload:
            payload["temperature"] = request.temperature

        return payload

    def _build_messages(self, messages: tuple[Message, ...]) -> list[dict]:
        result: list[dict] = []
        for msg in messages:
            result.append(self._convert_message(msg))
        # Prompt caching: add cache_control breakpoint on the last
        # content block of the last user message. This creates a cache
        # boundary at the most recent turn so prefix tokens up to this
        # point can be reused on the next request.
        for i in range(len(result) - 1, -1, -1):
            if result[i].get("role") == "user":
                content = result[i].get("content")
                if isinstance(content, list) and content:
                    content[-1]["cache_control"] = {"type": "ephemeral"}
                break
        return result

    def _convert_message(self, msg: Message) -> dict:
        # Tool result messages use Anthropic's format
        if len(msg.content) == 1 and isinstance(msg.content[0], ToolResultBlock):
            block = msg.content[0]
            return {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": block.tool_use_id,
                    "content": block.content,
                    **({"is_error": True} if block.is_error else {}),
                }],
            }

        # Multiple tool results in one message
        if all(isinstance(b, ToolResultBlock) for b in msg.content):
            return {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": b.tool_use_id,
                        "content": b.content,
                        **({"is_error": True} if b.is_error else {}),
                    }
                    for b in msg.content
                    if isinstance(b, ToolResultBlock)
                ],
            }

        # Build Anthropic content array
        content: list[dict] = []
        for block in msg.content:
            if isinstance(block, ThinkingBlock):
                # Round-trip signed thinking blocks verbatim
                entry: dict = {
                    "type": "thinking",
                    "thinking": block.content,
                }
                if block.signature:
                    entry["signature"] = block.signature
                content.append(entry)
            elif isinstance(block, TextBlock):
                content.append({"type": "text", "text": block.text})
            elif isinstance(block, ToolUseBlock):
                content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
            elif isinstance(block, ImageBlock):
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": block.media_type,
                        "data": block.data,
                    },
                })
            elif isinstance(block, ServerToolUseBlock):
                entry = {
                    "type": "server_tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                }
                if block.signature:
                    entry["signature"] = block.signature
                content.append(entry)
            elif isinstance(block, ServerToolResultBlock):
                entry = {
                    "type": "server_tool_result",
                    "tool_use_id": block.tool_use_id,
                    "content": block.content,
                }
                if block.signature:
                    entry["signature"] = block.signature
                content.append(entry)

        # Single text block — keep as content array (Anthropic wants it)
        return {"role": msg.role, "content": content}

    def _convert_tool(self, tool: ToolDefinition) -> dict:
        return {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
        }

    # ------------------------------------------------------------------
    # HTTP with retry
    # ------------------------------------------------------------------

    async def _post_with_retry(self, payload: dict) -> httpx.Response:
        if self._rate_handler is not None:
            return await self._post_via_rate_handler(payload)

        url = f"{self._base_url}/v1/messages"
        last_exc: Exception | None = None

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
                    continue
                raise
            except ProviderRateLimitError as exc:
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
                last_exc = ProviderTimeoutError(str(exc) or type(exc).__name__)
                if attempt < self._max_retries:
                    await asyncio.sleep(2 ** attempt)
                    attempt += 1
                    continue
                raise last_exc from exc
            attempt += 1

        raise last_exc  # type: ignore[misc]

    async def _post_via_rate_handler(self, payload: dict) -> httpx.Response:
        """C3b opt-in path: loop under the shared RateLimitHandler."""
        from llm_code.api.rate_limiter import (
            provider_taxonomy_anthropic,
            run_with_rate_limit,
        )

        url = f"{self._base_url}/v1/messages"

        async def attempt() -> httpx.Response:
            response = await self._client.post(url, json=payload)
            self._raise_for_status(response)
            return response

        return await run_with_rate_limit(
            attempt,
            self._rate_handler,
            taxonomy=provider_taxonomy_anthropic(),
        )

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code == 200:
            return
        try:
            body = response.json()
            msg = body.get("error", {}).get("message", response.text)
        except Exception:
            msg = response.text

        status = response.status_code
        if status == 401:
            raise ProviderAuthError(msg)
        if status == 404:
            raise ProviderModelNotFoundError(msg)
        if status == 429:
            retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            raise ProviderRateLimitError(msg, retry_after=retry_after)
        if status == 529:
            raise ProviderOverloadError(msg)
        if status >= 500:
            raise ProviderConnectionError(msg)
        raise ProviderConnectionError(f"HTTP {status}: {msg}")

    # ------------------------------------------------------------------
    # Non-streaming response parser
    # ------------------------------------------------------------------

    def _parse_response(self, response: httpx.Response) -> MessageResponse:
        data = response.json()

        content_blocks: list[ContentBlock] = []
        thinking_blocks: list[ThinkingBlock] = []

        for block in data.get("content", []):
            btype = block.get("type")
            if btype == "thinking":
                thinking_blocks.append(ThinkingBlock(
                    content=block.get("thinking", ""),
                    signature=block.get("signature", ""),
                ))
            elif btype == "text":
                content_blocks.append(TextBlock(text=block.get("text", "")))
            elif btype == "tool_use":
                content_blocks.append(ToolUseBlock(
                    id=block.get("id", ""),
                    name=block.get("name", ""),
                    input=block.get("input", {}),
                ))
            elif btype == "server_tool_use":
                content_blocks.append(ServerToolUseBlock(
                    id=block.get("id", ""),
                    name=block.get("name", ""),
                    input=block.get("input", {}),
                    signature=block.get("signature", ""),
                ))
            elif btype == "server_tool_result":
                content_blocks.append(ServerToolResultBlock(
                    tool_use_id=block.get("tool_use_id", ""),
                    content=block.get("content", ""),
                    signature=block.get("signature", ""),
                ))

        usage_data = data.get("usage", {})
        cache_read = int(usage_data.get("cache_read_input_tokens", 0))
        if self._prev_cache_read_tokens > 0 and cache_read > 0:
            drop = self._prev_cache_read_tokens - cache_read
            drop_pct = drop / self._prev_cache_read_tokens if self._prev_cache_read_tokens > 0 else 0
            if drop > 2000 and drop_pct > 0.05:
                _logger.warning(
                    "Cache breakpoint detected: cache_read dropped %d tokens (%.1f%%): %d → %d",
                    drop, drop_pct * 100, self._prev_cache_read_tokens, cache_read,
                )
        self._prev_cache_read_tokens = cache_read

        usage = TokenUsage(
            input_tokens=int(usage_data.get("input_tokens", 0)),
            output_tokens=int(usage_data.get("output_tokens", 0)),
            cache_read_tokens=cache_read,
            cache_creation_tokens=int(usage_data.get("cache_creation_input_tokens", 0)),
        )

        return MessageResponse(
            content=tuple(content_blocks),
            usage=usage,
            stop_reason=data.get("stop_reason", "end_turn"),
            thinking=tuple(thinking_blocks),
        )


# ── Streaming SSE iterator ────────────────────────────────────────────


class _AnthropicStreamIterator:
    """Async iterator over Anthropic SSE stream events.

    Anthropic's streaming format uses typed events::

        event: message_start
        data: {"type": "message_start", "message": {...}}

        event: content_block_start
        data: {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}}

        event: content_block_delta
        data: {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "..."}}

        event: content_block_stop
        data: {"type": "content_block_stop", "index": 0}

        event: message_delta
        data: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {...}}

        event: message_stop
        data: {"type": "message_stop"}
    """

    def __init__(self, raw: str) -> None:
        self._events: list[StreamEvent] = []
        self._index = 0
        self._parse(raw)

    def _parse(self, raw: str) -> None:
        events: list[StreamEvent] = []
        # Track block types by index
        block_types: dict[int, str] = {}
        block_ids: dict[int, str] = {}
        # Accumulate signature deltas per block index (thinking + server_tool_*)
        block_signatures: dict[int, list[str]] = {}
        # Accumulate server tool block data for final assembly
        server_blocks: dict[int, dict] = {}
        final_usage: dict = {}
        stop_reason = "end_turn"

        for block in _BLOCK_SEPARATOR.split(raw):
            block = block.strip()
            if not block:
                continue

            event_type = ""
            data_parts: list[str] = []

            for line in re.split(r"\r?\n", block):
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    value = line[5:]
                    if value.startswith(" "):
                        value = value[1:]
                    data_parts.append(value)

            if not data_parts:
                continue

            joined = "\n".join(data_parts)
            try:
                data = json.loads(joined)
            except json.JSONDecodeError:
                continue

            dtype = data.get("type", event_type)

            if dtype == "content_block_start":
                idx = data.get("index", 0)
                cb = data.get("content_block", {})
                cb_type = cb.get("type", "")
                block_types[idx] = cb_type
                if cb_type == "tool_use":
                    block_ids[idx] = cb.get("id", "")
                    events.append(StreamToolUseStart(
                        id=cb.get("id", ""),
                        name=cb.get("name", ""),
                    ))
                elif cb_type in ("server_tool_use", "server_tool_result"):
                    server_blocks[idx] = dict(cb)

            elif dtype == "content_block_delta":
                idx = data.get("index", 0)
                delta = data.get("delta", {})
                delta_type = delta.get("type", "")

                if delta_type == "thinking_delta":
                    text = delta.get("thinking", "")
                    if text:
                        events.append(StreamThinkingDelta(text=text))
                elif delta_type == "text_delta":
                    text = delta.get("text", "")
                    if text:
                        events.append(StreamTextDelta(text=text))
                elif delta_type == "input_json_delta":
                    partial = delta.get("partial_json", "")
                    if partial:
                        tool_id = block_ids.get(idx, "")
                        events.append(StreamToolUseInputDelta(
                            id=tool_id,
                            partial_json=partial,
                        ))
                elif delta_type == "signature_delta":
                    sig_part = delta.get("signature", "")
                    if sig_part:
                        block_signatures.setdefault(idx, []).append(sig_part)

            elif dtype == "content_block_stop":
                idx = data.get("index", 0)
                btype = block_types.get(idx, "")
                sig = "".join(block_signatures.pop(idx, []))
                # Emit accumulated signature for thinking blocks
                if btype == "thinking" and sig:
                    events.append(StreamThinkingSignature(signature=sig))
                # Emit server tool blocks with accumulated signatures
                elif btype == "server_tool_use" and idx in server_blocks:
                    sb = server_blocks.pop(idx)
                    events.append(StreamServerToolBlock(
                        block=ServerToolUseBlock(
                            id=sb.get("id", ""),
                            name=sb.get("name", ""),
                            input=sb.get("input", {}),
                            signature=sig,
                        )
                    ))
                elif btype == "server_tool_result" and idx in server_blocks:
                    sb = server_blocks.pop(idx)
                    # server_tool_result content can be a list or string
                    content = sb.get("content", "")
                    if isinstance(content, list):
                        content = json.dumps(content)
                    events.append(StreamServerToolBlock(
                        block=ServerToolResultBlock(
                            tool_use_id=sb.get("tool_use_id", ""),
                            content=content,
                            signature=sig,
                        )
                    ))

            elif dtype == "message_delta":
                delta = data.get("delta", {})
                stop_reason = delta.get("stop_reason", stop_reason)
                usage = data.get("usage", {})
                if usage:
                    final_usage.update(usage)

            elif dtype == "message_stop":
                # Final event — emit StreamMessageStop
                pass

            elif dtype == "message_start":
                msg = data.get("message", {})
                usage = msg.get("usage", {})
                if usage:
                    final_usage.update(usage)

            elif dtype == "error":
                error = data.get("error", {})
                error_msg = error.get("message", "Unknown stream error")
                _logger.warning("Anthropic stream error: %s", error_msg)

        # Emit final stop event
        usage = TokenUsage(
            input_tokens=int(final_usage.get("input_tokens", 0)),
            output_tokens=int(final_usage.get("output_tokens", 0)),
            cache_read_tokens=int(final_usage.get("cache_read_input_tokens", 0)),
            cache_creation_tokens=int(final_usage.get("cache_creation_input_tokens", 0)),
        )
        events.append(StreamMessageStop(usage=usage, stop_reason=stop_reason))
        self._events = events

    def __aiter__(self):
        return self

    async def __anext__(self) -> StreamEvent:
        if self._index >= len(self._events):
            raise StopAsyncIteration
        event = self._events[self._index]
        self._index += 1
        return event


class _AnthropicLiveStreamIterator:
    """True SSE streaming iterator — reads events as they arrive via httpx.

    Unlike ``_AnthropicStreamIterator`` which pre-parses the entire
    response body, this iterator opens an httpx streaming connection
    and yields ``StreamEvent`` objects as each SSE block arrives.
    This means the TUI sees the first token as soon as the API starts
    generating, rather than waiting for the entire response to download.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        payload: dict,
        max_retries: int,
    ) -> None:
        self._client = client
        self._url = f"{base_url}/v1/messages"
        self._payload = payload
        self._max_retries = max_retries
        # SSE parser state
        self._block_types: dict[int, str] = {}
        self._block_ids: dict[int, str] = {}
        self._block_signatures: dict[int, list[str]] = {}
        self._server_blocks: dict[int, dict] = {}
        self._final_usage: dict = {}
        self._stop_reason = "end_turn"
        # Buffered events from a single SSE block (may produce 0-N events)
        self._pending: list[StreamEvent] = []
        # The underlying httpx stream context + line iterator
        self._stream_ctx = None
        self._response = None
        self._line_iter = None
        self._done = False

    def __aiter__(self):
        return self

    async def _ensure_stream(self) -> None:
        """Open the streaming HTTP connection on first iteration."""
        if self._response is not None:
            return
        self._stream_ctx = self._client.stream(
            "POST", self._url, json=self._payload,
        )
        self._response = await self._stream_ctx.__aenter__()
        # Check for HTTP errors before consuming the stream
        status = self._response.status_code
        if status != 200:
            body = ""
            async for chunk in self._response.aiter_text():
                body += chunk
                if len(body) > 2000:
                    break
            await self._close()
            try:
                msg = json.loads(body).get("error", {}).get("message", body[:500])
            except Exception:
                msg = body[:500]
            if status == 401:
                raise ProviderAuthError(msg)
            if status == 404:
                raise ProviderModelNotFoundError(msg)
            if status == 429:
                raise ProviderRateLimitError(msg)
            if status == 529:
                raise ProviderOverloadError(msg)
            raise ProviderConnectionError(f"HTTP {status}: {msg}")
        self._line_iter = self._response.aiter_lines()

    async def _close(self) -> None:
        """Close the streaming connection."""
        if self._stream_ctx is not None:
            try:
                await self._stream_ctx.__aexit__(None, None, None)
            except Exception:
                pass
            self._stream_ctx = None
            self._response = None
            self._line_iter = None

    async def __anext__(self) -> StreamEvent:
        # Drain buffered events first
        if self._pending:
            return self._pending.pop(0)
        if self._done:
            raise StopAsyncIteration

        await self._ensure_stream()

        # Read SSE blocks from the line iterator.
        # An SSE block is terminated by a blank line.
        while True:
            event_type = ""
            data_parts: list[str] = []

            try:
                while True:
                    line = await self._line_iter.__anext__()
                    if not line:
                        # Blank line = end of SSE block
                        break
                    if line.startswith(":"):
                        continue
                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        value = line[5:]
                        if value.startswith(" "):
                            value = value[1:]
                        data_parts.append(value)
            except StopAsyncIteration:
                # Stream ended — emit final stop event and close
                self._done = True
                await self._close()
                usage = TokenUsage(
                    input_tokens=int(self._final_usage.get("input_tokens", 0)),
                    output_tokens=int(self._final_usage.get("output_tokens", 0)),
                    cache_read_tokens=int(self._final_usage.get("cache_read_input_tokens", 0)),
                    cache_creation_tokens=int(self._final_usage.get("cache_creation_input_tokens", 0)),
                )
                return StreamMessageStop(usage=usage, stop_reason=self._stop_reason)

            if not data_parts:
                continue

            joined = "\n".join(data_parts)
            try:
                data = json.loads(joined)
            except json.JSONDecodeError:
                continue

            events = self._process_sse_event(data, event_type)
            if events:
                # Return the first event, buffer the rest
                self._pending.extend(events[1:])
                return events[0]
            # No events from this SSE block — continue reading

    def _process_sse_event(self, data: dict, event_type: str) -> list[StreamEvent]:
        """Process a single parsed SSE event and return 0+ StreamEvents."""
        events: list[StreamEvent] = []
        dtype = data.get("type", event_type)

        if dtype == "content_block_start":
            idx = data.get("index", 0)
            cb = data.get("content_block", {})
            cb_type = cb.get("type", "")
            self._block_types[idx] = cb_type
            if cb_type == "tool_use":
                self._block_ids[idx] = cb.get("id", "")
                events.append(StreamToolUseStart(
                    id=cb.get("id", ""),
                    name=cb.get("name", ""),
                ))
            elif cb_type in ("server_tool_use", "server_tool_result"):
                self._server_blocks[idx] = dict(cb)

        elif dtype == "content_block_delta":
            idx = data.get("index", 0)
            delta = data.get("delta", {})
            delta_type = delta.get("type", "")

            if delta_type == "thinking_delta":
                text = delta.get("thinking", "")
                if text:
                    events.append(StreamThinkingDelta(text=text))
            elif delta_type == "text_delta":
                text = delta.get("text", "")
                if text:
                    events.append(StreamTextDelta(text=text))
            elif delta_type == "input_json_delta":
                partial = delta.get("partial_json", "")
                if partial:
                    tool_id = self._block_ids.get(idx, "")
                    events.append(StreamToolUseInputDelta(
                        id=tool_id,
                        partial_json=partial,
                    ))
            elif delta_type == "signature_delta":
                sig_part = delta.get("signature", "")
                if sig_part:
                    self._block_signatures.setdefault(idx, []).append(sig_part)

        elif dtype == "content_block_stop":
            idx = data.get("index", 0)
            btype = self._block_types.get(idx, "")
            sig = "".join(self._block_signatures.pop(idx, []))
            if btype == "thinking" and sig:
                events.append(StreamThinkingSignature(signature=sig))
            elif btype == "server_tool_use" and idx in self._server_blocks:
                sb = self._server_blocks.pop(idx)
                events.append(StreamServerToolBlock(
                    block=ServerToolUseBlock(
                        id=sb.get("id", ""),
                        name=sb.get("name", ""),
                        input=sb.get("input", {}),
                        signature=sig,
                    )
                ))
            elif btype == "server_tool_result" and idx in self._server_blocks:
                sb = self._server_blocks.pop(idx)
                content = sb.get("content", "")
                if isinstance(content, list):
                    content = json.dumps(content)
                events.append(StreamServerToolBlock(
                    block=ServerToolResultBlock(
                        tool_use_id=sb.get("tool_use_id", ""),
                        content=content,
                        signature=sig,
                    )
                ))

        elif dtype == "message_delta":
            delta = data.get("delta", {})
            self._stop_reason = delta.get("stop_reason", self._stop_reason)
            usage = data.get("usage", {})
            if usage:
                self._final_usage.update(usage)

        elif dtype == "message_start":
            msg = data.get("message", {})
            usage = msg.get("usage", {})
            if usage:
                self._final_usage.update(usage)

        elif dtype == "message_stop":
            # Will be handled by StopAsyncIteration from the line iterator
            pass

        elif dtype == "error":
            error = data.get("error", {})
            error_msg = error.get("message", "Unknown stream error")
            _logger.warning("Anthropic stream error: %s", error_msg)

        return events
