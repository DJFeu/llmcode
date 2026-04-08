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


# ---------------------------------------------------------------------------
# Round-2 Issue 2: Telemetry.span() must never break the caller, even when
# the underlying OTel context manager fails on enter or exit.
# ---------------------------------------------------------------------------


class _FakeStatusCode:
    OK = "StatusCode.OK"
    ERROR = "StatusCode.ERROR"


def _make_enabled_telemetry_with_tracer(tracer) -> Telemetry:
    t = Telemetry(TelemetryConfig(enabled=False))
    t._enabled = True
    t._tracer = tracer
    t._StatusCode = _FakeStatusCode
    return t


def test_span_swallows_tracer_start_exception() -> None:
    """If tracer.start_as_current_span itself raises, span() must yield None."""
    class _BoomTracer:
        def start_as_current_span(self, name):
            raise RuntimeError("tracer broken")

    t = _make_enabled_telemetry_with_tracer(_BoomTracer())
    with t.span("x") as span:
        assert span is None


def test_span_swallows_cm_enter_exception() -> None:
    """If the returned CM raises in __enter__, span() must yield None."""
    class _BoomCM:
        def __enter__(self):
            raise RuntimeError("enter broken")
        def __exit__(self, *a):
            return False

    class _Tracer:
        def start_as_current_span(self, name):
            return _BoomCM()

    t = _make_enabled_telemetry_with_tracer(_Tracer())
    # Must NOT raise — telemetry must never break the caller
    with t.span("x") as span:
        assert span is None


def test_span_swallows_cm_exit_exception() -> None:
    """If __exit__ raises on a clean yield, the caller must not see it."""
    class _Span:
        def set_attribute(self, *a, **k):
            pass
        def set_status(self, *a, **k):
            pass
        def record_exception(self, *a, **k):
            pass

    class _BoomExitCM:
        def __enter__(self):
            return _Span()
        def __exit__(self, *a):
            raise RuntimeError("exit broken")

    class _Tracer:
        def start_as_current_span(self, name):
            return _BoomExitCM()

    t = _make_enabled_telemetry_with_tracer(_Tracer())
    # Must NOT raise — outer guard restored in round-2 fix
    with t.span("x"):
        pass


def test_span_propagates_caller_exception_even_with_outer_guard() -> None:
    """Caller exceptions inside `with` block must still propagate (not swallowed)."""
    import pytest as _pytest

    class _Span:
        def set_attribute(self, *a, **k):
            pass
        def set_status(self, *a, **k):
            pass
        def record_exception(self, *a, **k):
            pass

    class _CM:
        def __enter__(self):
            return _Span()
        def __exit__(self, *a):
            return False

    class _Tracer:
        def start_as_current_span(self, name):
            return _CM()

    t = _make_enabled_telemetry_with_tracer(_Tracer())
    with _pytest.raises(ValueError, match="user code"):
        with t.span("x"):
            raise ValueError("user code")
