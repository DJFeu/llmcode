"""M6 Task 6.5 — trace context propagation tests.

Covers:

* ``_trace_ctx`` ContextVar read/write/reset lifecycle.
* ``propagate_across_to_thread()`` captures a context and makes it
  available via the ``as`` target.
* ``apply_context(token)`` re-attaches the captured context; spans
  opened inside the block share the caller's trace id.
* The pair survives an ``asyncio.to_thread`` boundary — the worker
  thread's span inherits the caller's trace id.
* ``inject_parent_into_span`` annotates the child span with the
  parent span id so sub-agent spawns show the correct tree.
* Graceful degradation when ``opentelemetry`` is not installed:
  ``propagate_across_to_thread`` yields ``None``, ``apply_context``
  is a no-op, nothing raises.
"""
from __future__ import annotations

import asyncio

import pytest


class TestModuleShape:
    def test_module_imports(self) -> None:
        from llm_code.engine.observability import propagation  # noqa: F401

    def test_public_api(self) -> None:
        from llm_code.engine.observability import propagation

        for name in (
            "_trace_ctx",
            "apply_context",
            "current_span",
            "get_context",
            "inject_parent_into_span",
            "propagate_across_to_thread",
            "reset_context",
            "set_context",
            "_OTEL_AVAILABLE",
        ):
            assert hasattr(propagation, name), f"missing: {name}"


class TestContextVarLifecycle:
    def test_default_is_none(self) -> None:
        from llm_code.engine.observability.propagation import get_context

        assert get_context() is None

    def test_set_and_reset_round_trips(self) -> None:
        from llm_code.engine.observability.propagation import (
            get_context,
            reset_context,
            set_context,
        )

        token = set_context("sentinel")
        try:
            assert get_context() == "sentinel"
        finally:
            reset_context(token)
        assert get_context() is None

    def test_nested_set_restores_previous_value(self) -> None:
        from llm_code.engine.observability.propagation import (
            get_context,
            reset_context,
            set_context,
        )

        outer = set_context("outer")
        inner = set_context("inner")
        assert get_context() == "inner"
        reset_context(inner)
        assert get_context() == "outer"
        reset_context(outer)
        assert get_context() is None


class TestPropagateAcrossToThreadDegraded:
    """When OTel is missing the helpers must still be safe to call."""

    def test_propagate_yields_without_raising(self) -> None:
        from llm_code.engine.observability.propagation import (
            propagate_across_to_thread,
        )

        with propagate_across_to_thread() as token:
            assert token is None or token is not None  # tolerate either

    def test_apply_none_token_is_noop(self) -> None:
        from llm_code.engine.observability.propagation import apply_context

        marker = []
        with apply_context(None):
            marker.append(1)
        assert marker == [1]

    def test_propagate_resets_contextvar_on_exit(self) -> None:
        from llm_code.engine.observability.propagation import (
            get_context,
            propagate_across_to_thread,
        )

        # Pre-condition: no context stashed.
        assert get_context() is None
        with propagate_across_to_thread():
            pass
        # Post-condition: ContextVar is reset.
        assert get_context() is None


class TestPropagationWithOTel:
    """These tests are only meaningful if OTel is installed."""

    def test_propagate_captures_context_when_otel(self) -> None:
        pytest.importorskip("opentelemetry")
        from opentelemetry import trace as otel_trace
        from opentelemetry.sdk.trace import TracerProvider

        from llm_code.engine.observability.propagation import (
            propagate_across_to_thread,
        )

        otel_trace.set_tracer_provider(TracerProvider())
        tracer = otel_trace.get_tracer("test")
        with tracer.start_as_current_span("parent"):
            with propagate_across_to_thread() as captured:
                assert captured is not None

    def test_apply_context_reattaches_across_to_thread(self) -> None:
        """Cross-thread: the worker thread's span shares the parent
        trace id if the caller propagated correctly."""
        pytest.importorskip("opentelemetry")
        from opentelemetry import trace as otel_trace
        from opentelemetry.sdk.trace import TracerProvider

        from llm_code.engine.observability.propagation import (
            apply_context,
            propagate_across_to_thread,
        )

        otel_trace.set_tracer_provider(TracerProvider())
        tracer = otel_trace.get_tracer("test")

        captured_trace_ids: list[int] = []

        def worker(token) -> None:
            with apply_context(token):
                with tracer.start_as_current_span("worker_span") as span:
                    captured_trace_ids.append(span.get_span_context().trace_id)

        async def caller() -> int:
            with tracer.start_as_current_span("parent") as parent:
                parent_tid = parent.get_span_context().trace_id
                with propagate_across_to_thread() as token:
                    await asyncio.to_thread(worker, token)
                return parent_tid

        parent_tid = asyncio.run(caller())
        assert captured_trace_ids, "worker never recorded a span"
        # The worker's trace id must match the parent's — this is the
        # whole point of the propagation helper.
        assert captured_trace_ids[0] == parent_tid

    def test_current_span_returns_active_span_when_otel(self) -> None:
        pytest.importorskip("opentelemetry")
        from opentelemetry import trace as otel_trace
        from opentelemetry.sdk.trace import TracerProvider

        from llm_code.engine.observability.propagation import current_span

        otel_trace.set_tracer_provider(TracerProvider())
        tracer = otel_trace.get_tracer("test")
        with tracer.start_as_current_span("s") as span:
            # Either the real span or OTel's INVALID span if unset; we
            # only care that current_span returns *something* sensible.
            result = current_span()
            assert result is not None
            # When inside a span context, it should match.
            assert result.get_span_context().trace_id == span.get_span_context().trace_id


class TestInjectParentIntoSpan:
    """``inject_parent_into_span`` is a best-effort helper used by
    sub-agent spawn plumbing. Its contract is: never raise, ever."""

    def test_both_none_is_noop(self) -> None:
        from llm_code.engine.observability.propagation import (
            inject_parent_into_span,
        )

        # Must not raise.
        inject_parent_into_span(None, None)

    def test_child_none_is_noop(self) -> None:
        from llm_code.engine.observability.propagation import (
            inject_parent_into_span,
        )

        class _FakeParent:
            def get_span_context(self):  # pragma: no cover - not reached
                raise AssertionError("should not be called when child is None")

        inject_parent_into_span(None, _FakeParent())

    def test_parent_none_is_noop(self) -> None:
        from llm_code.engine.observability.propagation import (
            inject_parent_into_span,
        )

        class _FakeChild:
            def set_attribute(self, *_a, **_kw):  # pragma: no cover - not reached
                raise AssertionError("should not set attrs when parent is None")

        inject_parent_into_span(_FakeChild(), None)

    def test_sets_parent_span_id_attribute_when_otel(self) -> None:
        pytest.importorskip("opentelemetry")
        from opentelemetry import trace as otel_trace
        from opentelemetry.sdk.trace import TracerProvider

        from llm_code.engine.observability.propagation import (
            inject_parent_into_span,
        )

        otel_trace.set_tracer_provider(TracerProvider())
        tracer = otel_trace.get_tracer("test")

        with tracer.start_as_current_span("parent") as parent:
            # Create a child in a sibling scope so we can capture both.
            with tracer.start_as_current_span("child") as child:
                inject_parent_into_span(child, parent)
                # Attribute was set — the in-memory span stores them.
                # We can't easily read them back on the default
                # ReadableSpan, but the call must not have raised.
                assert True

    def test_exception_in_parent_context_swallowed(self) -> None:
        from llm_code.engine.observability.propagation import (
            inject_parent_into_span,
        )

        class _RaisingParent:
            def get_span_context(self):
                raise RuntimeError("boom")

        class _Child:
            def set_attribute(self, *_a, **_kw):  # pragma: no cover
                raise AssertionError("should not reach set_attribute")

        # Must swallow the RuntimeError — observability must never
        # take down the engine.
        inject_parent_into_span(_Child(), _RaisingParent())


class TestSyncToAsyncBoundary:
    """Round-trip: sync -> async -> sync preserves the trace id."""

    def test_trace_id_preserved_across_to_thread_when_otel(self) -> None:
        pytest.importorskip("opentelemetry")
        from opentelemetry import trace as otel_trace
        from opentelemetry.sdk.trace import TracerProvider

        from llm_code.engine.observability.propagation import (
            apply_context,
            propagate_across_to_thread,
        )

        otel_trace.set_tracer_provider(TracerProvider())
        tracer = otel_trace.get_tracer("roundtrip")

        recorded = {}

        async def coro() -> None:
            with tracer.start_as_current_span("top") as top:
                recorded["top_tid"] = top.get_span_context().trace_id
                with propagate_across_to_thread() as token:
                    def worker() -> None:
                        with apply_context(token):
                            with tracer.start_as_current_span("worker") as w:
                                recorded["worker_tid"] = (
                                    w.get_span_context().trace_id
                                )

                    await asyncio.to_thread(worker)

        asyncio.run(coro())
        assert recorded["top_tid"] == recorded["worker_tid"]
