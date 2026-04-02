"""Tests for llm_code.api.openai_compat — TDD: written before implementation."""
from __future__ import annotations

import json
import pytest
import respx
import httpx

from llm_code.api.types import (
    Message,
    TextBlock,
    ImageBlock,
    ToolUseBlock,
    ToolResultBlock,
    MessageRequest,
    TokenUsage,
    StreamTextDelta,
    StreamToolUseStart,
    StreamToolUseInputDelta,
    StreamMessageStop,
)
from llm_code.api.errors import (
    ProviderAuthError,
    ProviderConnectionError,
    ProviderModelNotFoundError,
    ProviderRateLimitError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE_URL = "http://localhost:11434/v1"


def _make_provider(**kwargs):
    from llm_code.api.openai_compat import OpenAICompatProvider
    return OpenAICompatProvider(base_url=BASE_URL, **kwargs)


def _make_request(messages=None, tools=()):
    if messages is None:
        messages = (Message(role="user", content=(TextBlock(text="hello"),)),)
    return MessageRequest(
        model="qwen3",
        messages=messages,
        tools=tools,
        stream=False,
    )


def _text_response_json(text="Hello there!", input_tokens=10, output_tokens=5):
    return {
        "id": "chatcmpl-abc",
        "object": "chat.completion",
        "model": "qwen3",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    }


def _tool_call_response_json():
    return {
        "id": "chatcmpl-tool",
        "object": "chat.completion",
        "model": "qwen3",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_abc",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path": "/tmp/foo"}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 8, "completion_tokens": 12, "total_tokens": 20},
    }


# ---------------------------------------------------------------------------
# send_message — text response
# ---------------------------------------------------------------------------


class TestSendMessageText:
    @pytest.mark.asyncio
    @respx.mock
    async def test_send_message_text_response(self):
        respx.post(f"{BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_text_response_json())
        )
        provider = _make_provider()
        resp = await provider.send_message(_make_request())
        assert len(resp.content) == 1
        assert isinstance(resp.content[0], TextBlock)
        assert resp.content[0].text == "Hello there!"
        assert resp.usage == TokenUsage(input_tokens=10, output_tokens=5)
        assert resp.stop_reason == "stop"
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_send_message_uses_correct_endpoint(self):
        route = respx.post(f"{BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_text_response_json())
        )
        provider = _make_provider()
        await provider.send_message(_make_request())
        assert route.called
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_send_message_sends_model_in_payload(self):
        route = respx.post(f"{BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_text_response_json())
        )
        provider = _make_provider()
        await provider.send_message(_make_request())
        body = json.loads(route.calls[0].request.content)
        assert body["model"] == "qwen3"
        await provider.close()


# ---------------------------------------------------------------------------
# send_message — tool calls
# ---------------------------------------------------------------------------


class TestSendMessageToolCalls:
    @pytest.mark.asyncio
    @respx.mock
    async def test_send_message_with_tool_calls(self):
        respx.post(f"{BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_tool_call_response_json())
        )
        provider = _make_provider()
        resp = await provider.send_message(_make_request())
        assert len(resp.content) == 1
        block = resp.content[0]
        assert isinstance(block, ToolUseBlock)
        assert block.id == "call_abc"
        assert block.name == "read_file"
        assert block.input == {"path": "/tmp/foo"}
        assert resp.stop_reason == "tool_calls"
        await provider.close()


# ---------------------------------------------------------------------------
# Capability flags
# ---------------------------------------------------------------------------


class TestCapabilityFlags:
    def test_supports_native_tools_default(self):
        provider = _make_provider()
        assert provider.supports_native_tools() is True

    def test_supports_native_tools_false(self):
        provider = _make_provider(native_tools=False)
        assert provider.supports_native_tools() is False

    def test_supports_images(self):
        provider = _make_provider()
        assert provider.supports_images() is False


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


class TestErrorMapping:
    @pytest.mark.asyncio
    @respx.mock
    async def test_401_raises_auth_error(self):
        respx.post(f"{BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(401, json={"error": {"message": "Unauthorized"}})
        )
        provider = _make_provider()
        with pytest.raises(ProviderAuthError):
            await provider.send_message(_make_request())
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_404_raises_model_not_found(self):
        respx.post(f"{BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(404, json={"error": {"message": "Model not found"}})
        )
        provider = _make_provider()
        with pytest.raises(ProviderModelNotFoundError):
            await provider.send_message(_make_request())
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_429_raises_rate_limit_error(self):
        respx.post(f"{BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(429, json={"error": {"message": "Rate limited"}})
        )
        provider = _make_provider()
        with pytest.raises(ProviderRateLimitError):
            await provider.send_message(_make_request())
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_500_raises_connection_error(self):
        respx.post(f"{BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(500, json={"error": {"message": "Server error"}})
        )
        provider = _make_provider()
        with pytest.raises(ProviderConnectionError):
            await provider.send_message(_make_request())
        await provider.close()


# ---------------------------------------------------------------------------
# _build_messages
# ---------------------------------------------------------------------------


class TestBuildMessages:
    def test_build_messages_text(self):
        from llm_code.api.openai_compat import OpenAICompatProvider
        provider = OpenAICompatProvider(base_url=BASE_URL)
        msgs = (Message(role="user", content=(TextBlock(text="hi"),)),)
        result = provider._build_messages(msgs)
        assert result == [{"role": "user", "content": "hi"}]

    def test_build_messages_with_image(self):
        from llm_code.api.openai_compat import OpenAICompatProvider
        provider = OpenAICompatProvider(base_url=BASE_URL)
        msgs = (
            Message(
                role="user",
                content=(
                    TextBlock(text="describe this"),
                    ImageBlock(media_type="image/png", data="base64data"),
                ),
            ),
        )
        result = provider._build_messages(msgs)
        assert result[0]["role"] == "user"
        parts = result[0]["content"]
        assert isinstance(parts, list)
        assert parts[0] == {"type": "text", "text": "describe this"}
        assert parts[1] == {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,base64data"},
        }

    def test_build_messages_with_tool_result(self):
        from llm_code.api.openai_compat import OpenAICompatProvider
        provider = OpenAICompatProvider(base_url=BASE_URL)
        msgs = (
            Message(
                role="tool",
                content=(ToolResultBlock(tool_use_id="call_1", content="result text"),),
            ),
        )
        result = provider._build_messages(msgs)
        assert result[0]["role"] == "tool"
        assert result[0]["tool_call_id"] == "call_1"
        assert result[0]["content"] == "result text"

    def test_build_messages_system_as_first_message(self):
        from llm_code.api.openai_compat import OpenAICompatProvider
        provider = OpenAICompatProvider(base_url=BASE_URL)
        req = MessageRequest(
            model="qwen3",
            messages=(Message(role="user", content=(TextBlock(text="hello"),)),),
            system="You are helpful.",
        )
        msgs = provider._build_messages(req.messages, system=req.system)
        assert msgs[0] == {"role": "system", "content": "You are helpful."}
        assert msgs[1]["role"] == "user"


# ---------------------------------------------------------------------------
# stream_message
# ---------------------------------------------------------------------------


class TestStreamMessage:
    @pytest.mark.asyncio
    @respx.mock
    async def test_stream_message_yields_text_delta(self):
        sse_body = (
            'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}\n\n'
            'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":3,"total_tokens":8}}\n\n'
            "data: [DONE]\n\n"
        )
        respx.post(f"{BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, text=sse_body, headers={"content-type": "text/event-stream"})
        )
        provider = _make_provider()
        req = MessageRequest(
            model="qwen3",
            messages=(Message(role="user", content=(TextBlock(text="hi"),)),),
            stream=True,
        )
        events = []
        async for event in await provider.stream_message(req):
            events.append(event)

        text_events = [e for e in events if isinstance(e, StreamTextDelta)]
        stop_events = [e for e in events if isinstance(e, StreamMessageStop)]
        assert len(text_events) >= 1
        assert text_events[0].text == "Hello"
        assert len(stop_events) == 1
        assert stop_events[0].stop_reason == "stop"
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_stream_message_yields_tool_use(self):
        sse_body = (
            'data: {"id":"chatcmpl-2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_abc","type":"function","function":{"name":"read_file","arguments":""}}]},"finish_reason":null}]}\n\n'
            'data: {"id":"chatcmpl-2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"path\\": \\"/tmp/foo\\"}"}}]},"finish_reason":null}]}\n\n'
            'data: {"id":"chatcmpl-2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":8,"completion_tokens":10,"total_tokens":18}}\n\n'
            "data: [DONE]\n\n"
        )
        respx.post(f"{BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, text=sse_body, headers={"content-type": "text/event-stream"})
        )
        provider = _make_provider()
        req = MessageRequest(
            model="qwen3",
            messages=(Message(role="user", content=(TextBlock(text="call a tool"),)),),
            stream=True,
        )
        events = []
        async for event in await provider.stream_message(req):
            events.append(event)

        tool_starts = [e for e in events if isinstance(e, StreamToolUseStart)]
        tool_deltas = [e for e in events if isinstance(e, StreamToolUseInputDelta)]
        assert len(tool_starts) >= 1
        assert tool_starts[0].name == "read_file"
        assert len(tool_deltas) >= 1
        await provider.close()
