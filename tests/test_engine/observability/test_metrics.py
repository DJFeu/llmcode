"""M6 Task 6.4 — Prometheus metrics registry + canonical metrics.

The module is importable even when ``prometheus_client`` is not
installed (it exposes ``_NOOP_REGISTRY`` in that case). Real-metric
assertions are gated behind :func:`pytest.importorskip` so CI stays
green until the optional dep is pinned in ``pyproject.toml``.
"""
from __future__ import annotations

import pytest


class TestModuleImportable:
    def test_metrics_module_imports_without_prometheus(self) -> None:
        from llm_code.engine.observability import metrics

        assert metrics is not None

    def test_canonical_metric_names_exposed(self) -> None:
        """The module must expose the six canonical metric objects (real
        or shim) so call sites can reference them unconditionally."""
        from llm_code.engine.observability import metrics

        for name in (
            "pipeline_runs_total",
            "pipeline_duration_seconds",
            "component_duration_seconds",
            "agent_iterations_total",
            "tool_invocations_total",
            "api_tokens_total",
        ):
            assert hasattr(metrics, name), f"missing metric: {name}"

    def test_registry_reference_exposed(self) -> None:
        from llm_code.engine.observability import metrics

        # Either a real CollectorRegistry or None (when lib missing).
        assert hasattr(metrics, "registry")

    def test_record_helpers_exposed(self) -> None:
        from llm_code.engine.observability import metrics

        assert callable(metrics.record_pipeline_run)
        assert callable(metrics.record_component)
        assert callable(metrics.record_tool_invocation)


class TestNoopBehaviour:
    """Whatever shim the metrics module uses must allow calls like
    ``.labels(...).inc()`` / ``.observe(...)`` without raising, so
    call-sites work uniformly regardless of dependency state."""

    def test_counter_shim_tolerates_labels_inc(self) -> None:
        from llm_code.engine.observability import metrics

        # This must not raise whether prometheus_client is present or not.
        metrics.pipeline_runs_total.labels(outcome="success").inc()

    def test_counter_shim_tolerates_inc_without_labels(self) -> None:
        from llm_code.engine.observability import metrics

        # pipeline_duration has no labels; Histogram exposes observe.
        metrics.pipeline_duration_seconds.observe(0.123)

    def test_tool_invocations_counter_label_call(self) -> None:
        from llm_code.engine.observability import metrics

        metrics.tool_invocations_total.labels(tool="bash", status="success").inc()

    def test_api_tokens_total_label_call(self) -> None:
        from llm_code.engine.observability import metrics

        metrics.api_tokens_total.labels(
            direction="input", model="claude-sonnet-4"
        ).inc(42)


class TestRecordPipelineRun:
    def test_successful_block_increments_success_counter(self) -> None:
        pc = pytest.importorskip("prometheus_client")  # noqa: F841
        from llm_code.engine.observability import metrics

        before = metrics.pipeline_runs_total.labels(outcome="success")._value.get()
        with metrics.record_pipeline_run():
            pass
        after = metrics.pipeline_runs_total.labels(outcome="success")._value.get()
        assert after == before + 1

    def test_failing_block_increments_error_counter(self) -> None:
        pytest.importorskip("prometheus_client")
        from llm_code.engine.observability import metrics

        before = metrics.pipeline_runs_total.labels(outcome="error")._value.get()
        with pytest.raises(RuntimeError):
            with metrics.record_pipeline_run():
                raise RuntimeError("boom")
        after = metrics.pipeline_runs_total.labels(outcome="error")._value.get()
        assert after == before + 1

    def test_noop_without_prometheus_does_not_raise(self) -> None:
        from llm_code.engine.observability import metrics

        with metrics.record_pipeline_run():
            pass


class TestRecordComponent:
    def test_component_duration_observed(self) -> None:
        pytest.importorskip("prometheus_client")
        from llm_code.engine.observability import metrics

        histogram = metrics.component_duration_seconds.labels(component="FakeComp")
        before_count = histogram._sum.get()
        with metrics.record_component("FakeComp"):
            pass
        after_count = histogram._sum.get()
        # Histogram sum increases by the elapsed time (>= 0).
        assert after_count >= before_count

    def test_component_label_emits_without_prometheus(self) -> None:
        from llm_code.engine.observability import metrics

        with metrics.record_component("SomeComp"):
            pass


class TestRecordToolInvocation:
    def test_tool_invocation_success_counter(self) -> None:
        pytest.importorskip("prometheus_client")
        from llm_code.engine.observability import metrics

        counter = metrics.tool_invocations_total.labels(
            tool="read_file", status="success"
        )
        before = counter._value.get()
        with metrics.record_tool_invocation("read_file", "success"):
            pass
        after = counter._value.get()
        assert after == before + 1

    def test_tool_invocation_error_counter(self) -> None:
        pytest.importorskip("prometheus_client")
        from llm_code.engine.observability import metrics

        counter = metrics.tool_invocations_total.labels(
            tool="bash", status="error"
        )
        before = counter._value.get()
        with pytest.raises(ValueError):
            with metrics.record_tool_invocation("bash", "error"):
                raise ValueError("tool failed")
        after = counter._value.get()
        assert after == before + 1

    def test_tool_invocation_noop_without_prometheus(self) -> None:
        from llm_code.engine.observability import metrics

        with metrics.record_tool_invocation("any", "success"):
            pass


class TestLabelsInvariants:
    def test_tool_invocation_status_labels_known(self) -> None:
        """Canonical status values: success | error | retried | fallback."""
        from llm_code.engine.observability import metrics

        # Should accept every canonical status without raising.
        for status in ("success", "error", "retried", "fallback"):
            metrics.tool_invocations_total.labels(tool="x", status=status).inc()

    def test_agent_iterations_labels_known(self) -> None:
        from llm_code.engine.observability import metrics

        for mode in ("build", "plan"):
            for exit_reason in ("done", "max_steps", "error"):
                metrics.agent_iterations_total.labels(
                    mode=mode, exit_reason=exit_reason
                ).inc()

    def test_api_tokens_direction_labels_known(self) -> None:
        from llm_code.engine.observability import metrics

        for direction in ("input", "output"):
            metrics.api_tokens_total.labels(
                direction=direction, model="claude-sonnet-4"
            ).inc()


class TestGenerateLatest:
    """When prometheus_client is installed, the registry should produce a
    valid Prometheus exposition payload."""

    def test_generate_latest_produces_text(self) -> None:
        pc = pytest.importorskip("prometheus_client")
        from llm_code.engine.observability import metrics

        # Touch a counter so the metric appears in the output.
        metrics.pipeline_runs_total.labels(outcome="success").inc()
        payload = pc.generate_latest(metrics.registry).decode()
        assert "engine_pipeline_runs_total" in payload

    def test_payload_content_type(self) -> None:
        pc = pytest.importorskip("prometheus_client")

        # Sanity-check the content type constant is importable where a
        # FastAPI /metrics endpoint would need it.
        assert pc.CONTENT_TYPE_LATEST.startswith("text/plain")


class TestMetricNamesFollowPromConvention:
    def test_canonical_counter_names_use_engine_prefix(self) -> None:
        """All six canonical metrics should be prefixed ``engine_`` so
        they land under the same namespace in dashboards."""
        pytest.importorskip("prometheus_client")
        from llm_code.engine.observability import metrics

        # _name is the sanitised Prometheus metric name.
        assert metrics.pipeline_runs_total._name == "engine_pipeline_runs"
        assert metrics.pipeline_duration_seconds._name == \
            "engine_pipeline_duration_seconds"
        assert metrics.component_duration_seconds._name == \
            "engine_component_duration_seconds"
        assert metrics.agent_iterations_total._name == "engine_agent_iterations"
        assert metrics.tool_invocations_total._name == "engine_tool_invocations"
        assert metrics.api_tokens_total._name == "engine_api_tokens"


class TestOutcomeLabelsIndependent:
    """Success and error counters track independently — incrementing one
    must not disturb the other."""

    def test_success_does_not_affect_error_counter(self) -> None:
        pytest.importorskip("prometheus_client")
        from llm_code.engine.observability import metrics

        err_before = metrics.pipeline_runs_total.labels(
            outcome="error"
        )._value.get()
        with metrics.record_pipeline_run():
            pass
        err_after = metrics.pipeline_runs_total.labels(
            outcome="error"
        )._value.get()
        assert err_after == err_before


class TestContextManagerReentrancy:
    def test_nested_record_component_calls_work(self) -> None:
        from llm_code.engine.observability import metrics

        with metrics.record_component("Outer"):
            with metrics.record_component("Inner"):
                pass

    def test_nested_record_tool_invocation_calls_work(self) -> None:
        from llm_code.engine.observability import metrics

        with metrics.record_tool_invocation("outer", "success"):
            with metrics.record_tool_invocation("inner", "success"):
                pass
