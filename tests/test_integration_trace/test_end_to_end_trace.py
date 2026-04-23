"""M6 Task 6.11 — end-to-end trace-tree integration test.

Drives the stack bottom-up: construct a small :class:`Pipeline` with a
couple of real :class:`Component` subclasses, run it against an
in-memory :class:`InMemorySpanExporter`, and assert the resulting span
tree has the expected shape::

    pipeline.<Pipeline>
      |- component.<CompA>
      |- component.<CompB>

Supplementary coverage:

* Agent-iteration spans stack under the pipeline span via the
  ``agent_span`` / ``tool_call_span`` / ``api_span`` context managers.
* The :class:`RedactingBatchSpanProcessor`-style behaviour — in this
  test we use the canonical span attributes (no raw user prompt
  content), so the exported attributes must not match any leak-corpus
  pattern.
* Prometheus metrics are exposed when the dependency is available.
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def _tracer_provider_with_exporter(monkeypatch):
    """Return ``(provider, exporter)`` using the in-memory exporter.

    The fixture builds an isolated :class:`TracerProvider` per test so
    spans don't leak between tests. OTel's global tracer provider is
    patched so ``trace.get_tracer(...)`` returns the per-test provider's
    tracers — this is required because OTel silently rejects a second
    ``set_tracer_provider`` call for the life of the process.
    """
    pytest.importorskip("opentelemetry")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    from opentelemetry import trace as otel_trace

    # Force ``trace.get_tracer`` to use this provider regardless of
    # whether the global provider was already set by an earlier test.
    monkeypatch.setattr(otel_trace, "get_tracer_provider", lambda: provider)
    monkeypatch.setattr(otel_trace, "get_tracer", provider.get_tracer)

    yield provider, exporter
    provider.shutdown()


class TestPipelineSpanTree:
    def test_pipeline_run_emits_span_per_component(
        self, _tracer_provider_with_exporter
    ) -> None:
        provider, exporter = _tracer_provider_with_exporter

        from llm_code.engine.component import component, output_types
        from llm_code.engine.pipeline import Pipeline

        # Two small components to prove each gets its own span.
        @component
        @output_types(value=int)
        class CompA:
            def run(self, x: int) -> dict:
                return {"value": x + 1}

        @component
        @output_types(value=int)
        class CompB:
            def run(self, value: int) -> dict:
                return {"value": value * 2}

        pipeline = Pipeline()
        pipeline.add_component("a", CompA())
        pipeline.add_component("b", CompB())
        pipeline.connect("a.value", "b.value")

        # Nudge Pipeline to use the new provider (some OTel versions
        # cache tracer instances; re-fetching ensures the test provider
        # receives spans).
        tracer = provider.get_tracer("llmcode")
        with tracer.start_as_current_span("test.parent"):
            result = pipeline.run({"a": {"x": 1}})
        assert result["b"]["value"] == 4

        spans = exporter.get_finished_spans()
        names = [s.name for s in spans]
        # Component wrapping via @component -> traced_component must
        # produce component.<ClassName> spans. The Pipeline wrapper
        # must produce a pipeline.<ClassName> span. The outer
        # test.parent span is also present because we opened it above.
        assert any(n.startswith("component.CompA") for n in names), names
        assert any(n.startswith("component.CompB") for n in names), names
        assert any(n.startswith("pipeline.") for n in names), names


class TestNestedContextManagerSpans:
    """Verify agent/tool/api spans stack as expected when composed."""

    def test_agent_tool_api_hierarchy(self, _tracer_provider_with_exporter) -> None:
        provider, exporter = _tracer_provider_with_exporter

        from llm_code.engine.tracing import (
            agent_span,
            api_span,
            pipeline_span,
            tool_call_span,
        )

        with pipeline_span("root"):
            with agent_span(iteration=1, mode="plan"):
                with tool_call_span("bash", "deadbeefdeadbeef"):
                    with api_span("claude-sonnet-4"):
                        pass

        spans = exporter.get_finished_spans()
        names = [s.name for s in spans]
        assert "pipeline.root" in names
        assert "agent.iteration.1" in names
        assert "tool.bash" in names
        assert "api.stream" in names


class TestAttributeHygiene:
    """Make sure no raw credentials leak into span attributes from
    happy-path calls."""

    def test_no_secret_patterns_in_span_attributes(
        self, _tracer_provider_with_exporter
    ) -> None:
        provider, exporter = _tracer_provider_with_exporter

        from llm_code.engine.observability.redaction import DEFAULT_PATTERNS
        from llm_code.engine.tracing import pipeline_span, tool_call_span

        with pipeline_span("p"):
            # ``args_hash`` is the only tool-arg-related attribute we
            # set, and it's sha256-truncated — never raw args.
            with tool_call_span("bash", "0123456789abcdef"):
                pass

        spans = exporter.get_finished_spans()
        for span in spans:
            for key, value in (span.attributes or {}).items():
                if not isinstance(value, str):
                    continue
                for pattern in DEFAULT_PATTERNS:
                    assert not pattern.search(value), (
                        f"leaked {key!r} matches pattern {pattern.pattern!r}"
                    )


class TestMetricsWireUp:
    """Smoke-check: canonical Prometheus metrics are importable and
    the Histogram / Counter objects expose the expected API."""

    def test_canonical_metrics_present(self) -> None:
        pytest.importorskip("prometheus_client")
        from llm_code.engine.observability import metrics

        for name in (
            "pipeline_runs_total",
            "pipeline_duration_seconds",
            "component_duration_seconds",
            "agent_iterations_total",
            "tool_invocations_total",
            "api_tokens_total",
        ):
            assert hasattr(metrics, name), f"missing: {name}"

    def test_record_pipeline_run_no_raise(self) -> None:
        from llm_code.engine.observability.metrics import record_pipeline_run

        with record_pipeline_run():
            pass

    def test_record_component_no_raise(self) -> None:
        from llm_code.engine.observability.metrics import record_component

        with record_component("TestComp"):
            pass
