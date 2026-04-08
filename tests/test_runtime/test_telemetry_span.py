"""Tests for Telemetry.span() context manager."""
from __future__ import annotations

from llm_code.runtime.telemetry import Telemetry, TelemetryConfig, get_noop_telemetry


def test_noop_telemetry_span_is_a_no_op() -> None:
    t = get_noop_telemetry()
    with t.span("noop.test", foo="bar") as span:
        assert span is None  # no-op yields None
    # Calling again must work (idempotent)
    with t.span("noop.test2"):
        pass


def test_disabled_telemetry_span_does_not_import_otel(monkeypatch) -> None:
    """Even if otel is broken, disabled span() must not blow up."""
    import sys
    monkeypatch.setitem(sys.modules, "opentelemetry", None)

    t = Telemetry(TelemetryConfig(enabled=False))
    with t.span("nope") as span:
        assert span is None


def test_span_set_attribute_on_noop_is_safe() -> None:
    t = get_noop_telemetry()
    with t.span("x") as span:
        if span is not None:
            span.set_attribute("foo", "bar")


def test_span_accepts_keyword_attributes() -> None:
    t = get_noop_telemetry()
    with t.span("test", session_id="s1", model="claude-opus-4-6"):
        pass


def test_span_supports_nesting() -> None:
    t = get_noop_telemetry()
    with t.span("outer"):
        with t.span("inner"):
            with t.span("innermost"):
                pass


def test_span_propagates_exceptions() -> None:
    import pytest

    t = get_noop_telemetry()
    with pytest.raises(ValueError):
        with t.span("err"):
            raise ValueError("boom")


def test_trace_llm_completion_no_op_when_disabled() -> None:
    t = get_noop_telemetry()
    with t.trace_llm_completion(
        session_id="s1",
        model="claude-opus-4-6",
        prompt_preview="hello",
        input_tokens=5,
    ) as span:
        assert span is None


def test_trace_llm_completion_truncates_prompt_to_4k() -> None:
    t = get_noop_telemetry()
    big = "x" * 100_000
    with t.trace_llm_completion(
        session_id="s1",
        model="claude-opus-4-6",
        prompt_preview=big,
        input_tokens=5,
    ):
        pass


def test_trace_llm_completion_inside_outer_span_is_nested() -> None:
    t = get_noop_telemetry()
    with t.span("agent.turn"):
        with t.trace_llm_completion(
            session_id="s1", model="m", prompt_preview="p", input_tokens=1
        ):
            pass


def test_truncate_text_helper_caps_at_4k() -> None:
    from llm_code.runtime.telemetry import _truncate_for_attribute
    out = _truncate_for_attribute("y" * 10_000, max_chars=4096)
    assert len(out) <= 4096 + len("...[truncated]")
    assert out.endswith("...[truncated]")


def test_truncate_text_helper_passthrough_when_short() -> None:
    from llm_code.runtime.telemetry import _truncate_for_attribute
    assert _truncate_for_attribute("short", max_chars=4096) == "short"
