"""Tests for llm_code.api.types — TDD: written before implementation."""
import dataclasses
import pytest

from llm_code.api.types import (
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    ImageBlock,
    Message,
    ToolDefinition,
    MessageRequest,
    TokenUsage,
    MessageResponse,
    StreamEvent,
    StreamMessageStart,
    StreamTextDelta,
    StreamToolUseStart,
    StreamToolUseInputDelta,
    StreamMessageStop,
    ContentBlock,
)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestTextBlock:
    def test_constructable(self):
        b = TextBlock(text="hello")
        assert b.text == "hello"

    def test_frozen(self):
        b = TextBlock(text="hello")
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            b.text = "mutated"  # type: ignore[misc]


class TestToolUseBlock:
    def test_constructable(self):
        b = ToolUseBlock(id="t1", name="bash", input={"cmd": "ls"})
        assert b.id == "t1"
        assert b.name == "bash"
        assert b.input == {"cmd": "ls"}

    def test_frozen(self):
        b = ToolUseBlock(id="t1", name="bash", input={})
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            b.name = "mutated"  # type: ignore[misc]


class TestToolResultBlock:
    def test_constructable_with_defaults(self):
        b = ToolResultBlock(tool_use_id="t1", content="ok")
        assert b.tool_use_id == "t1"
        assert b.content == "ok"
        assert b.is_error is False

    def test_constructable_with_error(self):
        b = ToolResultBlock(tool_use_id="t1", content="fail", is_error=True)
        assert b.is_error is True

    def test_frozen(self):
        b = ToolResultBlock(tool_use_id="t1", content="ok")
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            b.content = "mutated"  # type: ignore[misc]


class TestImageBlock:
    def test_constructable(self):
        b = ImageBlock(media_type="image/png", data="base64data")
        assert b.media_type == "image/png"
        assert b.data == "base64data"

    def test_frozen(self):
        b = ImageBlock(media_type="image/png", data="x")
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            b.data = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ContentBlock union type alias
# ---------------------------------------------------------------------------

class TestContentBlockUnion:
    def test_text_block_is_content_block(self):
        b: ContentBlock = TextBlock(text="hi")
        assert isinstance(b, TextBlock)

    def test_tool_use_block_is_content_block(self):
        b: ContentBlock = ToolUseBlock(id="1", name="x", input={})
        assert isinstance(b, ToolUseBlock)

    def test_tool_result_block_is_content_block(self):
        b: ContentBlock = ToolResultBlock(tool_use_id="1", content="r")
        assert isinstance(b, ToolResultBlock)

    def test_image_block_is_content_block(self):
        b: ContentBlock = ImageBlock(media_type="image/jpeg", data="d")
        assert isinstance(b, ImageBlock)


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------

class TestMessage:
    def test_constructable(self):
        content = (TextBlock(text="hi"),)
        m = Message(role="user", content=content)
        assert m.role == "user"
        assert m.content == content

    def test_content_is_tuple(self):
        m = Message(role="user", content=(TextBlock(text="a"),))
        assert isinstance(m.content, tuple)

    def test_frozen(self):
        m = Message(role="user", content=(TextBlock(text="a"),))
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            m.role = "assistant"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ToolDefinition
# ---------------------------------------------------------------------------

class TestToolDefinition:
    def test_constructable(self):
        t = ToolDefinition(name="bash", description="run bash", input_schema={"type": "object"})
        assert t.name == "bash"
        assert t.description == "run bash"
        assert t.input_schema == {"type": "object"}

    def test_frozen(self):
        t = ToolDefinition(name="bash", description="run bash", input_schema={})
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            t.name = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# MessageRequest
# ---------------------------------------------------------------------------

class TestMessageRequest:
    def test_constructable_minimal(self):
        req = MessageRequest(
            model="qwen3",
            messages=(Message(role="user", content=(TextBlock(text="hi"),)),),
        )
        assert req.model == "qwen3"
        assert req.system is None
        assert req.tools == ()
        assert req.max_tokens == 4096
        assert req.temperature == 0.7
        assert req.stream is True

    def test_constructable_full(self):
        req = MessageRequest(
            model="qwen3",
            messages=(Message(role="user", content=(TextBlock(text="hi"),)),),
            system="You are helpful",
            tools=(ToolDefinition(name="bash", description="run bash", input_schema={}),),
            max_tokens=1024,
            temperature=0.0,
            stream=False,
        )
        assert req.system == "You are helpful"
        assert len(req.tools) == 1
        assert req.max_tokens == 1024
        assert req.temperature == 0.0
        assert req.stream is False

    def test_frozen(self):
        req = MessageRequest(
            model="qwen3",
            messages=(Message(role="user", content=(TextBlock(text="hi"),)),),
        )
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            req.model = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TokenUsage
# ---------------------------------------------------------------------------

class TestTokenUsage:
    def test_constructable(self):
        u = TokenUsage(input_tokens=10, output_tokens=20)
        assert u.input_tokens == 10
        assert u.output_tokens == 20

    def test_frozen(self):
        u = TokenUsage(input_tokens=10, output_tokens=20)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            u.input_tokens = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# MessageResponse
# ---------------------------------------------------------------------------

class TestMessageResponse:
    def test_constructable(self):
        usage = TokenUsage(input_tokens=5, output_tokens=10)
        resp = MessageResponse(
            content=(TextBlock(text="response"),),
            usage=usage,
            stop_reason="end_turn",
        )
        assert resp.stop_reason == "end_turn"
        assert resp.usage.input_tokens == 5

    def test_frozen(self):
        usage = TokenUsage(input_tokens=5, output_tokens=10)
        resp = MessageResponse(
            content=(TextBlock(text="r"),),
            usage=usage,
            stop_reason="end_turn",
        )
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            resp.stop_reason = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Stream events
# ---------------------------------------------------------------------------

class TestStreamEvents:
    def test_stream_event_is_base(self):
        assert issubclass(StreamMessageStart, StreamEvent)
        assert issubclass(StreamTextDelta, StreamEvent)
        assert issubclass(StreamToolUseStart, StreamEvent)
        assert issubclass(StreamToolUseInputDelta, StreamEvent)
        assert issubclass(StreamMessageStop, StreamEvent)

    def test_stream_message_start(self):
        e = StreamMessageStart(model="qwen3")
        assert e.model == "qwen3"

    def test_stream_text_delta(self):
        e = StreamTextDelta(text="chunk")
        assert e.text == "chunk"

    def test_stream_tool_use_start(self):
        e = StreamToolUseStart(id="t1", name="bash")
        assert e.id == "t1"
        assert e.name == "bash"

    def test_stream_tool_use_input_delta(self):
        e = StreamToolUseInputDelta(id="t1", partial_json='{"cm')
        assert e.partial_json == '{"cm'

    def test_stream_message_stop(self):
        usage = TokenUsage(input_tokens=1, output_tokens=2)
        e = StreamMessageStop(usage=usage, stop_reason="end_turn")
        assert e.stop_reason == "end_turn"
        assert e.usage.output_tokens == 2

    def test_stream_events_frozen(self):
        e = StreamTextDelta(text="chunk")
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            e.text = "mutated"  # type: ignore[misc]
