"""M9: OpenTelemetry-style tracing skeleton."""
from __future__ import annotations

from llm_code.runtime.tracing import (
    NoopSpan,
    get_tracer,
    instrument_tool,
)


class TestNoopSpan:
    def test_context_manager(self) -> None:
        span = NoopSpan("x")
        with span:
            pass

    def test_set_attribute_accepts_anything(self) -> None:
        span = NoopSpan("x")
        span.set_attribute("k", "v")
        span.set_attribute("i", 42)
        span.set_attribute("obj", {"a": 1})

    def test_add_event(self) -> None:
        NoopSpan("x").add_event("start")

    def test_record_exception(self) -> None:
        try:
            raise ValueError("boom")
        except ValueError as exc:
            NoopSpan("x").record_exception(exc)


class TestTracer:
    def test_get_tracer_returns_callable(self) -> None:
        tracer = get_tracer()
        span = tracer.start_span("x")
        with span:
            pass

    def test_instrument_tool_decorator_runs_wrapped(self) -> None:
        calls = []

        @instrument_tool("test_tool")
        def wrapped(args):
            calls.append(args)
            return "ok"

        result = wrapped({"k": 1})
        assert result == "ok"
        assert calls == [{"k": 1}]

    def test_instrument_propagates_exceptions(self) -> None:
        @instrument_tool("test_tool")
        def boom(args):
            raise RuntimeError("oops")

        import pytest
        with pytest.raises(RuntimeError):
            boom({})
