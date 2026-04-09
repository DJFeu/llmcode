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
    StreamEvent,
    StreamMessageStop,
    StreamTextDelta,
    StreamThinkingDelta,
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
    ) -> None:
        self._api_key = api_key
        self._model_name = model_name
        self._max_retries = max_retries
        self._timeout = timeout
        self._base_url = base_url.rstrip("/")

        self._client = httpx.AsyncClient(
            headers={
                "x-api-key": api_key,
                "anthropic-version": _API_VERSION,
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
        response = await self._post_with_retry(payload)
        return _AnthropicStreamIterator(response.text)

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
            payload["system"] = request.system

        if request.tools:
            payload["tools"] = [self._convert_tool(t) for t in request.tools]

        if stream:
            payload["stream"] = True

        # Extended thinking: pass budget via extra_body or detect config
        if request.extra_body:
            # Anthropic-specific: thinking budget
            thinking_cfg = request.extra_body.get("chat_template_kwargs", {})
            if thinking_cfg.get("enable_thinking"):
                budget = thinking_cfg.get("thinking_budget", 10000)
                payload["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": budget,
                }
            # Pass through any other extra_body keys
            for k, v in request.extra_body.items():
                if k not in ("chat_template_kwargs",):
                    payload[k] = v

        if request.temperature is not None and "thinking" not in payload:
            payload["temperature"] = request.temperature

        return payload

    def _build_messages(self, messages: tuple[Message, ...]) -> list[dict]:
        result: list[dict] = []
        for msg in messages:
            result.append(self._convert_message(msg))
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

        usage_data = data.get("usage", {})
        usage = TokenUsage(
            input_tokens=int(usage_data.get("input_tokens", 0)),
            output_tokens=int(usage_data.get("output_tokens", 0)),
            cache_read_tokens=int(usage_data.get("cache_read_input_tokens", 0)),
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
                    # Signature deltas are accumulated by the runtime
                    # during thinking assembly (P4). We don't emit a
                    # separate event — the final signature is on the
                    # content_block_stop. For now, just skip.
                    pass

            elif dtype == "content_block_stop":
                # Nothing special needed — block assembly is done by
                # the runtime's post-stream logic.
                pass

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
