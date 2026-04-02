"""OpenAI-compatible provider implementation."""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Sequence

import httpx

from llm_code.api.errors import (
    ProviderAuthError,
    ProviderConnectionError,
    ProviderModelNotFoundError,
    ProviderRateLimitError,
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

        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.post(url, json=payload)
                self._raise_for_status(response)
                return response
            except (ProviderConnectionError, ProviderRateLimitError) as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise
            except (ProviderAuthError, ProviderModelNotFoundError):
                raise
            except httpx.ConnectError as exc:
                last_exc = ProviderConnectionError(str(exc))
                if attempt < self._max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise last_exc from exc

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
            raise ProviderRateLimitError(msg)
        if response.status_code >= 500:
            raise ProviderConnectionError(msg)
        # Other 4xx — treat as connection error
        raise ProviderConnectionError(f"HTTP {response.status_code}: {msg}")

    def _parse_response(self, response: httpx.Response) -> MessageResponse:
        data = response.json()
        choice = data["choices"][0]
        message = choice["message"]
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
        usage = TokenUsage(
            input_tokens=usage_data.get("prompt_tokens", 0),
            output_tokens=usage_data.get("completion_tokens", 0),
        )

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

        for chunk in self._events:
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

                # Stop event — emitted once at the end
                if finish_reason:
                    usage_data = chunk.get("usage") or {}
                    usage = TokenUsage(
                        input_tokens=usage_data.get("prompt_tokens", 0),
                        output_tokens=usage_data.get("completion_tokens", 0),
                    )
                    events.append(
                        StreamMessageStop(usage=usage, stop_reason=finish_reason)
                    )

        self._processed = events

    def __aiter__(self):
        return self

    async def __anext__(self) -> StreamEvent:
        if self._index >= len(self._processed):
            raise StopAsyncIteration
        event = self._processed[self._index]
        self._index += 1
        return event
