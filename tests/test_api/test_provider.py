"""Tests for llm_code.api.provider — TDD: written before implementation."""
import pytest
from typing import AsyncIterator

from llm_code.api.provider import LLMProvider
from llm_code.api.types import (
    MessageRequest,
    MessageResponse,
    Message,
    TextBlock,
    TokenUsage,
    StreamEvent,
    StreamTextDelta,
    StreamMessageStop,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request() -> MessageRequest:
    return MessageRequest(
        model="qwen3",
        messages=(Message(role="user", content=(TextBlock(text="hello"),)),),
    )


def _make_response() -> MessageResponse:
    return MessageResponse(
        content=(TextBlock(text="hi"),),
        usage=TokenUsage(input_tokens=5, output_tokens=3),
        stop_reason="end_turn",
    )


# ---------------------------------------------------------------------------
# ABC cannot be instantiated
# ---------------------------------------------------------------------------

class TestABCNotInstantiable:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            LLMProvider()  # type: ignore[abstract]

    def test_partial_subclass_not_instantiable(self):
        class Partial(LLMProvider):
            async def send_message(self, request: MessageRequest) -> MessageResponse:
                return _make_response()
            # stream_message, supports_native_tools, supports_images NOT implemented

        with pytest.raises(TypeError):
            Partial()


# ---------------------------------------------------------------------------
# Concrete mock subclass
# ---------------------------------------------------------------------------

class MockProvider(LLMProvider):
    def __init__(self, native_tools: bool = True, images: bool = False):
        self._native_tools = native_tools
        self._images = images

    async def send_message(self, request: MessageRequest) -> MessageResponse:
        return _make_response()

    async def stream_message(self, request: MessageRequest) -> AsyncIterator[StreamEvent]:
        async def _gen():
            yield StreamTextDelta(text="chunk1")
            yield StreamTextDelta(text="chunk2")
            yield StreamMessageStop(
                usage=TokenUsage(input_tokens=5, output_tokens=6),
                stop_reason="end_turn",
            )
        return _gen()

    def supports_native_tools(self) -> bool:
        return self._native_tools

    def supports_images(self) -> bool:
        return self._images


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------

class TestSendMessage:
    @pytest.mark.asyncio
    async def test_send_message_returns_response(self):
        provider = MockProvider()
        req = _make_request()
        resp = await provider.send_message(req)
        assert isinstance(resp, MessageResponse)

    @pytest.mark.asyncio
    async def test_send_message_response_has_content(self):
        provider = MockProvider()
        resp = await provider.send_message(_make_request())
        assert len(resp.content) > 0

    @pytest.mark.asyncio
    async def test_send_message_response_has_usage(self):
        provider = MockProvider()
        resp = await provider.send_message(_make_request())
        assert isinstance(resp.usage, TokenUsage)

    @pytest.mark.asyncio
    async def test_send_message_response_has_stop_reason(self):
        provider = MockProvider()
        resp = await provider.send_message(_make_request())
        assert resp.stop_reason == "end_turn"


# ---------------------------------------------------------------------------
# stream_message
# ---------------------------------------------------------------------------

class TestStreamMessage:
    @pytest.mark.asyncio
    async def test_stream_message_yields_events(self):
        provider = MockProvider()
        events: list[StreamEvent] = []
        async for event in await provider.stream_message(_make_request()):
            events.append(event)
        assert len(events) > 0

    @pytest.mark.asyncio
    async def test_stream_message_yields_text_deltas(self):
        provider = MockProvider()
        events: list[StreamEvent] = []
        async for event in await provider.stream_message(_make_request()):
            events.append(event)
        text_events = [e for e in events if isinstance(e, StreamTextDelta)]
        assert len(text_events) == 2
        assert text_events[0].text == "chunk1"
        assert text_events[1].text == "chunk2"

    @pytest.mark.asyncio
    async def test_stream_message_ends_with_stop(self):
        provider = MockProvider()
        events: list[StreamEvent] = []
        async for event in await provider.stream_message(_make_request()):
            events.append(event)
        assert isinstance(events[-1], StreamMessageStop)
        assert events[-1].stop_reason == "end_turn"


# ---------------------------------------------------------------------------
# Capability flags
# ---------------------------------------------------------------------------

class TestCapabilityFlags:
    def test_supports_native_tools_true(self):
        provider = MockProvider(native_tools=True)
        assert provider.supports_native_tools() is True

    def test_supports_native_tools_false(self):
        provider = MockProvider(native_tools=False)
        assert provider.supports_native_tools() is False

    def test_supports_images_true(self):
        provider = MockProvider(images=True)
        assert provider.supports_images() is True

    def test_supports_images_false(self):
        provider = MockProvider(images=False)
        assert provider.supports_images() is False


# ---------------------------------------------------------------------------
# Interface contract
# ---------------------------------------------------------------------------

class TestInterface:
    def test_abstract_methods_listed(self):
        assert hasattr(LLMProvider, '__abstractmethods__')
        methods = LLMProvider.__abstractmethods__
        assert 'send_message' in methods
        assert 'stream_message' in methods
        assert 'supports_native_tools' in methods
        assert 'supports_images' in methods

    def test_mock_is_provider(self):
        assert isinstance(MockProvider(), LLMProvider)
