"""Tests verifying span nesting in the conversation runner."""
from __future__ import annotations

import pytest

from llm_code.runtime.telemetry import Telemetry, TelemetryConfig


class _RecordingSpan:
    def __init__(self, name: str, parent: "_RecordingSpan | None" = None) -> None:
        self.name = name
        self.parent = parent
        self.attributes: dict = {}
        self.children: list[_RecordingSpan] = []
        self.status_ok = False
        self.status_err = False

    def set_attribute(self, key, value) -> None:
        self.attributes[key] = value

    def set_status(self, status) -> None:
        if str(status).endswith("OK"):
            self.status_ok = True
        elif str(status).endswith("ERROR"):
            self.status_err = True

    def record_exception(self, exc) -> None:
        self.attributes["exception"] = str(exc)


class _RecordingTracer:
    def __init__(self) -> None:
        self.root_spans: list[_RecordingSpan] = []
        self._stack: list[_RecordingSpan] = []

    def start_as_current_span(self, name: str):
        parent = self._stack[-1] if self._stack else None
        span = _RecordingSpan(name, parent)
        if parent is None:
            self.root_spans.append(span)
        else:
            parent.children.append(span)
        tracer = self

        class _CM:
            def __enter__(self_inner):
                tracer._stack.append(span)
                return span
            def __exit__(self_inner, exc_type, exc_val, exc_tb):
                tracer._stack.pop()
                return False
        return _CM()


class _FakeStatusCode:
    OK = "StatusCode.OK"
    ERROR = "StatusCode.ERROR"


@pytest.fixture
def recording_telemetry() -> tuple[Telemetry, _RecordingTracer]:
    t = Telemetry(TelemetryConfig(enabled=False))
    rec = _RecordingTracer()
    t._enabled = True
    t._tracer = rec
    t._StatusCode = _FakeStatusCode
    return t, rec


def test_agent_turn_span_nests_llm_completion_and_tool(recording_telemetry) -> None:
    t, rec = recording_telemetry

    with t.span("agent.turn", session_id="s1") as turn:
        with t.trace_llm_completion(
            session_id="s1", model="m", prompt_preview="p", input_tokens=1
        ):
            pass
        with t.span("tool.read_file", tool_name="read_file") as _tool_span:
            pass

    assert len(rec.root_spans) == 1
    root = rec.root_spans[0]
    assert root.name == "agent.turn"
    assert root.attributes.get("session_id") == "s1"

    child_names = [c.name for c in root.children]
    assert "llm.completion" in child_names
    assert "tool.read_file" in child_names


def test_nested_span_attributes_are_recorded(recording_telemetry) -> None:
    t, rec = recording_telemetry
    with t.trace_llm_completion(
        session_id="s2",
        model="claude-opus-4-6",
        prompt_preview="hello world",
        completion_preview="hi there",
        input_tokens=10,
        output_tokens=5,
        provider="anthropic",
        finish_reason="end_turn",
    ):
        pass
    span = rec.root_spans[0]
    assert span.name == "llm.completion"
    assert span.attributes["llm.model"] == "claude-opus-4-6"
    assert span.attributes["llm.tokens.input"] == 10
    assert span.attributes["llm.tokens.output"] == 5
    assert span.attributes["llm.tokens.total"] == 15
    assert "hello world" in span.attributes["llm.prompt.preview"]
    assert "hi there" in span.attributes["llm.completion.preview"]
    assert span.attributes["llm.provider"] == "anthropic"
    assert span.status_ok is True


def test_exception_inside_span_is_recorded_and_propagates(recording_telemetry) -> None:
    t, rec = recording_telemetry
    with pytest.raises(ValueError):
        with t.span("test.error"):
            raise ValueError("boom")
    span = rec.root_spans[0]
    assert span.status_err is True
    assert "boom" in span.attributes.get("exception", "")


def test_build_prompt_preview_handles_text_blocks() -> None:
    from dataclasses import dataclass
    from llm_code.runtime.conversation import _build_prompt_preview

    @dataclass
    class _Block:
        text: str

    @dataclass
    class _Msg:
        role: str
        content: tuple

    msgs = [
        _Msg(role="user", content=(_Block(text="hello world"),)),
        _Msg(role="assistant", content=(_Block(text="hi there"),)),
    ]
    out = _build_prompt_preview(msgs)
    assert "[user]" in out
    assert "[assistant]" in out
    assert "hello world" in out
    assert "hi there" in out


def test_build_prompt_preview_truncates_long_messages() -> None:
    from llm_code.runtime.conversation import _build_prompt_preview

    class _Msg:
        def __init__(self, text: str) -> None:
            self.role = "user"
            self.content = text

    msgs = [_Msg("x" * 5000) for _ in range(3)]
    out = _build_prompt_preview(msgs, max_chars=1000)
    assert len(out) <= 1000


def test_build_prompt_preview_empty_returns_empty() -> None:
    from llm_code.runtime.conversation import _build_prompt_preview
    assert _build_prompt_preview([]) == ""
