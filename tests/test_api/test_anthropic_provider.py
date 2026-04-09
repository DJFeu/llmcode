"""Tests for the native Anthropic Messages API provider."""
from __future__ import annotations


import httpx
import pytest

from llm_code.api.anthropic_provider import (
    AnthropicProvider,
    _AnthropicStreamIterator,
)
from llm_code.api.errors import (
    ProviderAuthError,
    ProviderModelNotFoundError,
    ProviderOverloadError,
    ProviderRateLimitError,
)
from llm_code.api.types import (
    ImageBlock,
    Message,
    MessageRequest,
    StreamMessageStop,
    StreamTextDelta,
    StreamThinkingDelta,
    StreamToolUseInputDelta,
    StreamToolUseStart,
    TextBlock,
    ThinkingBlock,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)


# ---------- Helpers ----------


def _make_provider(**kw) -> AnthropicProvider:
    defaults = {"api_key": "test-key", "model_name": "claude-sonnet-4-6"}
    defaults.update(kw)
    return AnthropicProvider(**defaults)


def _make_request(**kw) -> MessageRequest:
    defaults = {
        "model": "claude-sonnet-4-6",
        "messages": (Message(role="user", content=(TextBlock(text="Hello"),)),),
    }
    defaults.update(kw)
    return MessageRequest(**defaults)


# ---------- Protocol surface ----------


def test_supports_native_tools() -> None:
    p = _make_provider()
    assert p.supports_native_tools() is True


def test_supports_images() -> None:
    p = _make_provider()
    assert p.supports_images() is True


def test_supports_reasoning() -> None:
    p = _make_provider()
    assert p.supports_reasoning() is True


# ---------- Message conversion ----------


class TestMessageConversion:
    def test_simple_text_message(self) -> None:
        p = _make_provider()
        msg = Message(role="user", content=(TextBlock(text="Hello"),))
        result = p._convert_message(msg)
        assert result == {
            "role": "user",
            "content": [{"type": "text", "text": "Hello"}],
        }

    def test_tool_result_message(self) -> None:
        p = _make_provider()
        msg = Message(
            role="user",
            content=(ToolResultBlock(tool_use_id="t1", content="done"),),
        )
        result = p._convert_message(msg)
        assert result["role"] == "user"
        assert result["content"][0]["type"] == "tool_result"
        assert result["content"][0]["tool_use_id"] == "t1"
        assert result["content"][0]["content"] == "done"
        assert "is_error" not in result["content"][0]

    def test_tool_result_error(self) -> None:
        p = _make_provider()
        msg = Message(
            role="user",
            content=(ToolResultBlock(tool_use_id="t1", content="fail", is_error=True),),
        )
        result = p._convert_message(msg)
        assert result["content"][0]["is_error"] is True

    def test_thinking_block_round_trip(self) -> None:
        """Signed thinking blocks must be preserved verbatim."""
        p = _make_provider()
        msg = Message(
            role="assistant",
            content=(
                ThinkingBlock(content="Let me think...", signature="abc123"),
                TextBlock(text="The answer is 42."),
            ),
        )
        result = p._convert_message(msg)
        assert result["role"] == "assistant"
        assert len(result["content"]) == 2
        thinking = result["content"][0]
        assert thinking["type"] == "thinking"
        assert thinking["thinking"] == "Let me think..."
        assert thinking["signature"] == "abc123"
        text = result["content"][1]
        assert text["type"] == "text"
        assert text["text"] == "The answer is 42."

    def test_thinking_block_unsigned(self) -> None:
        """Unsigned thinking blocks omit signature field."""
        p = _make_provider()
        msg = Message(
            role="assistant",
            content=(
                ThinkingBlock(content="reasoning here", signature=""),
                TextBlock(text="answer"),
            ),
        )
        result = p._convert_message(msg)
        thinking = result["content"][0]
        assert "signature" not in thinking

    def test_tool_use_block(self) -> None:
        p = _make_provider()
        msg = Message(
            role="assistant",
            content=(
                ToolUseBlock(id="tu1", name="read_file", input={"path": "/tmp/a"}),
            ),
        )
        result = p._convert_message(msg)
        block = result["content"][0]
        assert block["type"] == "tool_use"
        assert block["id"] == "tu1"
        assert block["name"] == "read_file"
        assert block["input"] == {"path": "/tmp/a"}

    def test_image_block(self) -> None:
        p = _make_provider()
        msg = Message(
            role="user",
            content=(
                TextBlock(text="What's this?"),
                ImageBlock(media_type="image/png", data="base64data"),
            ),
        )
        result = p._convert_message(msg)
        assert len(result["content"]) == 2
        img = result["content"][1]
        assert img["type"] == "image"
        assert img["source"]["type"] == "base64"
        assert img["source"]["data"] == "base64data"

    def test_multiple_tool_results(self) -> None:
        p = _make_provider()
        msg = Message(
            role="user",
            content=(
                ToolResultBlock(tool_use_id="t1", content="result1"),
                ToolResultBlock(tool_use_id="t2", content="result2"),
            ),
        )
        result = p._convert_message(msg)
        assert len(result["content"]) == 2
        assert result["content"][0]["tool_use_id"] == "t1"
        assert result["content"][1]["tool_use_id"] == "t2"


# ---------- Payload building ----------


class TestPayloadBuilding:
    def test_basic_payload(self) -> None:
        p = _make_provider()
        req = _make_request()
        payload = p._build_payload(req, stream=False)
        assert payload["model"] == "claude-sonnet-4-6"
        assert payload["max_tokens"] == 4096
        assert "stream" not in payload
        assert len(payload["messages"]) == 1

    def test_stream_payload(self) -> None:
        p = _make_provider()
        req = _make_request()
        payload = p._build_payload(req, stream=True)
        assert payload["stream"] is True

    def test_system_prompt(self) -> None:
        p = _make_provider()
        req = _make_request(system="You are helpful.")
        payload = p._build_payload(req, stream=False)
        # System prompt is a content block array with cache_control
        assert payload["system"] == [
            {
                "type": "text",
                "text": "You are helpful.",
                "cache_control": {"type": "ephemeral"},
            }
        ]

    def test_tools_in_payload(self) -> None:
        p = _make_provider()
        tools = (ToolDefinition(
            name="read_file",
            description="Read a file",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        ),)
        req = _make_request(tools=tools)
        payload = p._build_payload(req, stream=False)
        assert len(payload["tools"]) == 1
        assert payload["tools"][0]["name"] == "read_file"
        assert "input_schema" in payload["tools"][0]
        # Last tool should have cache_control
        assert payload["tools"][-1]["cache_control"] == {"type": "ephemeral"}

    def test_thinking_enabled(self) -> None:
        p = _make_provider()
        req = _make_request(extra_body={
            "chat_template_kwargs": {
                "enable_thinking": True,
                "thinking_budget": 5000,
            },
        })
        payload = p._build_payload(req, stream=False)
        assert payload["thinking"]["type"] == "enabled"
        assert payload["thinking"]["budget_tokens"] == 5000
        # Temperature not set when thinking is enabled
        assert "temperature" not in payload

    def test_temperature_without_thinking(self) -> None:
        p = _make_provider()
        req = _make_request(temperature=0.5)
        payload = p._build_payload(req, stream=False)
        assert payload["temperature"] == 0.5


# ---------- Response parsing ----------


class TestResponseParsing:
    def test_parse_text_response(self) -> None:
        p = _make_provider()
        response = httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "Hello!"}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "stop_reason": "end_turn",
            },
        )
        result = p._parse_response(response)
        assert len(result.content) == 1
        assert isinstance(result.content[0], TextBlock)
        assert result.content[0].text == "Hello!"
        assert result.usage.input_tokens == 10
        assert result.usage.output_tokens == 5

    def test_parse_thinking_response(self) -> None:
        p = _make_provider()
        response = httpx.Response(
            200,
            json={
                "content": [
                    {"type": "thinking", "thinking": "Let me think...", "signature": "sig123"},
                    {"type": "text", "text": "The answer."},
                ],
                "usage": {"input_tokens": 20, "output_tokens": 50},
                "stop_reason": "end_turn",
            },
        )
        result = p._parse_response(response)
        assert len(result.thinking) == 1
        assert result.thinking[0].content == "Let me think..."
        assert result.thinking[0].signature == "sig123"
        assert len(result.content) == 1
        assert result.content[0].text == "The answer."

    def test_parse_tool_use_response(self) -> None:
        p = _make_provider()
        response = httpx.Response(
            200,
            json={
                "content": [
                    {"type": "tool_use", "id": "tu1", "name": "read_file", "input": {"path": "/tmp"}},
                ],
                "usage": {"input_tokens": 15, "output_tokens": 10},
                "stop_reason": "tool_use",
            },
        )
        result = p._parse_response(response)
        assert len(result.content) == 1
        assert isinstance(result.content[0], ToolUseBlock)
        assert result.content[0].name == "read_file"
        assert result.stop_reason == "tool_use"

    def test_parse_cache_tokens(self) -> None:
        p = _make_provider()
        response = httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "ok"}],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 10,
                    "cache_read_input_tokens": 80,
                    "cache_creation_input_tokens": 20,
                },
                "stop_reason": "end_turn",
            },
        )
        result = p._parse_response(response)
        assert result.usage.cache_read_tokens == 80
        assert result.usage.cache_creation_tokens == 20


# ---------- Error handling ----------


class TestErrorHandling:
    def test_401_raises_auth_error(self) -> None:
        p = _make_provider()
        response = httpx.Response(
            401,
            json={"error": {"message": "Invalid API key"}},
        )
        with pytest.raises(ProviderAuthError):
            p._raise_for_status(response)

    def test_404_raises_model_not_found(self) -> None:
        p = _make_provider()
        response = httpx.Response(
            404,
            json={"error": {"message": "Model not found"}},
        )
        with pytest.raises(ProviderModelNotFoundError):
            p._raise_for_status(response)

    def test_429_raises_rate_limit(self) -> None:
        p = _make_provider()
        response = httpx.Response(
            429,
            json={"error": {"message": "Rate limited"}},
            headers={"Retry-After": "30"},
        )
        with pytest.raises(ProviderRateLimitError) as exc_info:
            p._raise_for_status(response)
        assert exc_info.value.retry_after == 30.0

    def test_529_raises_overload(self) -> None:
        p = _make_provider()
        response = httpx.Response(
            529,
            json={"error": {"message": "Overloaded"}},
        )
        with pytest.raises(ProviderOverloadError):
            p._raise_for_status(response)


# ---------- Streaming SSE ----------


class TestStreamIterator:
    @pytest.mark.asyncio
    async def test_simple_text_stream(self) -> None:
        raw = (
            "event: message_start\n"
            'data: {"type": "message_start", "message": {"usage": {"input_tokens": 10}}}\n\n'
            "event: content_block_start\n"
            'data: {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}}\n\n'
            "event: content_block_delta\n"
            'data: {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hello"}}\n\n'
            "event: content_block_delta\n"
            'data: {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": " world"}}\n\n'
            "event: content_block_stop\n"
            'data: {"type": "content_block_stop", "index": 0}\n\n'
            "event: message_delta\n"
            'data: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 5}}\n\n'
            "event: message_stop\n"
            'data: {"type": "message_stop"}\n\n'
        )
        it = _AnthropicStreamIterator(raw)
        events = [e async for e in it]

        text_events = [e for e in events if isinstance(e, StreamTextDelta)]
        assert len(text_events) == 2
        assert text_events[0].text == "Hello"
        assert text_events[1].text == " world"

        stop = [e for e in events if isinstance(e, StreamMessageStop)]
        assert len(stop) == 1
        assert stop[0].stop_reason == "end_turn"
        assert stop[0].usage.input_tokens == 10
        assert stop[0].usage.output_tokens == 5

    @pytest.mark.asyncio
    async def test_thinking_stream(self) -> None:
        raw = (
            "event: message_start\n"
            'data: {"type": "message_start", "message": {"usage": {"input_tokens": 10}}}\n\n'
            "event: content_block_start\n"
            'data: {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}}\n\n'
            "event: content_block_delta\n"
            'data: {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "Let me think..."}}\n\n'
            "event: content_block_stop\n"
            'data: {"type": "content_block_stop", "index": 0}\n\n'
            "event: content_block_start\n"
            'data: {"type": "content_block_start", "index": 1, "content_block": {"type": "text"}}\n\n'
            "event: content_block_delta\n"
            'data: {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "The answer."}}\n\n'
            "event: content_block_stop\n"
            'data: {"type": "content_block_stop", "index": 1}\n\n'
            "event: message_delta\n"
            'data: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 20}}\n\n'
            "event: message_stop\n"
            'data: {"type": "message_stop"}\n\n'
        )
        it = _AnthropicStreamIterator(raw)
        events = [e async for e in it]

        thinking = [e for e in events if isinstance(e, StreamThinkingDelta)]
        assert len(thinking) == 1
        assert thinking[0].text == "Let me think..."

        text = [e for e in events if isinstance(e, StreamTextDelta)]
        assert len(text) == 1
        assert text[0].text == "The answer."

    @pytest.mark.asyncio
    async def test_tool_use_stream(self) -> None:
        raw = (
            "event: message_start\n"
            'data: {"type": "message_start", "message": {"usage": {"input_tokens": 10}}}\n\n'
            "event: content_block_start\n"
            'data: {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "tu1", "name": "read_file"}}\n\n'
            "event: content_block_delta\n"
            'data: {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": "{\\"path\\""}}\n\n'
            "event: content_block_delta\n"
            'data: {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": ": \\"/tmp\\"}"}}\n\n'
            "event: content_block_stop\n"
            'data: {"type": "content_block_stop", "index": 0}\n\n'
            "event: message_delta\n"
            'data: {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 15}}\n\n'
            "event: message_stop\n"
            'data: {"type": "message_stop"}\n\n'
        )
        it = _AnthropicStreamIterator(raw)
        events = [e async for e in it]

        tool_starts = [e for e in events if isinstance(e, StreamToolUseStart)]
        assert len(tool_starts) == 1
        assert tool_starts[0].id == "tu1"
        assert tool_starts[0].name == "read_file"

        json_deltas = [e for e in events if isinstance(e, StreamToolUseInputDelta)]
        assert len(json_deltas) == 2
        assert json_deltas[0].id == "tu1"

        stop = [e for e in events if isinstance(e, StreamMessageStop)]
        assert stop[0].stop_reason == "tool_use"

    @pytest.mark.asyncio
    async def test_empty_stream(self) -> None:
        """Empty stream still emits a stop event."""
        raw = (
            "event: message_start\n"
            'data: {"type": "message_start", "message": {"usage": {}}}\n\n'
            "event: message_delta\n"
            'data: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {}}\n\n'
            "event: message_stop\n"
            'data: {"type": "message_stop"}\n\n'
        )
        it = _AnthropicStreamIterator(raw)
        events = [e async for e in it]
        assert any(isinstance(e, StreamMessageStop) for e in events)


# ---------- Factory routing ----------


def test_factory_routes_claude_to_anthropic() -> None:
    """ProviderClient.from_model routes claude-* models to AnthropicProvider."""
    import os
    os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
    from llm_code.api.client import ProviderClient
    provider = ProviderClient.from_model(
        model="claude-sonnet-4-6",
        api_key="test-key",
    )
    assert type(provider).__name__ == "AnthropicProvider"
