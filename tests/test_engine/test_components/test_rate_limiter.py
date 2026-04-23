"""Tests for :class:`RateLimiterComponent` — v12 M2 Task 2.7 Step 2.

Wraps the policy helpers in :mod:`llm_code.api.rate_limiter` as a
Pipeline stage. The Component does *not* perform HTTP retries on its
own — the outer loop still lives in the Agent (M3). The Component's
job is to decide, before the tool/provider call happens, whether the
current retry counters already exhaust the budget and what sleep
should follow.

Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-pipeline-dag.md Task 2.7 Step 2
"""
from __future__ import annotations

import pytest

from llm_code.api.rate_limiter import (
    ExceptionTaxonomy,
    RateLimitClassification,
    RequestKind,
)


class TestRateLimiterComponentImports:
    def test_module_imports(self) -> None:
        from llm_code.engine.components import rate_limiter as rl_mod

        assert hasattr(rl_mod, "RateLimiterComponent")


class TestRateLimiterComponentShape:
    def test_marked_as_component(self) -> None:
        from llm_code.engine.component import is_component
        from llm_code.engine.components.rate_limiter import RateLimiterComponent

        assert is_component(RateLimiterComponent())

    def test_input_sockets(self) -> None:
        from llm_code.engine.component import get_input_sockets
        from llm_code.engine.components.rate_limiter import RateLimiterComponent

        inputs = get_input_sockets(RateLimiterComponent)
        assert "proceed" in inputs
        assert "classification" in inputs
        assert "retry_after" in inputs

    def test_output_sockets(self) -> None:
        from llm_code.engine.component import get_output_sockets
        from llm_code.engine.components.rate_limiter import RateLimiterComponent

        outputs = get_output_sockets(RateLimiterComponent)
        assert set(outputs) == {"proceed", "sleep_seconds", "reason"}


class TestRateLimiterComponentRun:
    def test_proceed_false_short_circuits(self) -> None:
        from llm_code.engine.components.rate_limiter import RateLimiterComponent

        comp = RateLimiterComponent()
        out = comp.run(
            proceed=False,
            classification=RateLimitClassification.OK.value,
            retry_after=None,
        )
        assert out["proceed"] is False
        assert out["sleep_seconds"] == 0.0

    def test_ok_classification_passes_through(self) -> None:
        from llm_code.engine.components.rate_limiter import RateLimiterComponent

        comp = RateLimiterComponent()
        out = comp.run(
            proceed=True,
            classification=RateLimitClassification.OK.value,
            retry_after=None,
        )
        assert out["proceed"] is True
        assert out["sleep_seconds"] == 0.0

    def test_rate_limit_triggers_backoff(self) -> None:
        from llm_code.engine.components.rate_limiter import RateLimiterComponent

        comp = RateLimiterComponent()
        out = comp.run(
            proceed=True,
            classification=RateLimitClassification.RATE_LIMIT.value,
            retry_after=None,
        )
        assert out["proceed"] is True
        assert out["sleep_seconds"] > 0

    def test_rate_limit_honours_retry_after(self) -> None:
        from llm_code.engine.components.rate_limiter import RateLimiterComponent

        comp = RateLimiterComponent()
        out = comp.run(
            proceed=True,
            classification=RateLimitClassification.RATE_LIMIT.value,
            retry_after=5.0,
        )
        assert out["sleep_seconds"] == 5.0

    def test_permanent_error_short_circuits(self) -> None:
        from llm_code.engine.components.rate_limiter import RateLimiterComponent

        comp = RateLimiterComponent()
        out = comp.run(
            proceed=True,
            classification=RateLimitClassification.PERMANENT.value,
            retry_after=None,
        )
        assert out["proceed"] is False

    def test_overload_classification_backoff(self) -> None:
        from llm_code.engine.components.rate_limiter import RateLimiterComponent

        comp = RateLimiterComponent()
        out = comp.run(
            proceed=True,
            classification=RateLimitClassification.OVERLOAD.value,
            retry_after=None,
        )
        assert out["sleep_seconds"] > 0

    def test_reason_populated_on_denial(self) -> None:
        from llm_code.engine.components.rate_limiter import RateLimiterComponent

        comp = RateLimiterComponent()
        out = comp.run(
            proceed=True,
            classification=RateLimitClassification.PERMANENT.value,
            retry_after=None,
        )
        assert out["reason"] != ""

    def test_background_mode_rate_limit_bails(self) -> None:
        from llm_code.engine.components.rate_limiter import RateLimiterComponent

        comp = RateLimiterComponent(request_kind=RequestKind.BACKGROUND)
        out = comp.run(
            proceed=True,
            classification=RateLimitClassification.RATE_LIMIT.value,
            retry_after=None,
        )
        assert out["proceed"] is False

    def test_foreground_rate_limit_retries(self) -> None:
        from llm_code.engine.components.rate_limiter import RateLimiterComponent

        comp = RateLimiterComponent(request_kind=RequestKind.FOREGROUND)
        out = comp.run(
            proceed=True,
            classification=RateLimitClassification.RATE_LIMIT.value,
            retry_after=None,
        )
        assert out["proceed"] is True

    def test_counters_persist_across_calls(self) -> None:
        """Successive RATE_LIMIT calls should eventually exhaust the
        foreground budget."""
        from llm_code.engine.components.rate_limiter import RateLimiterComponent

        comp = RateLimiterComponent()
        # 11 iterations to overshoot the foreground budget (10).
        out = {"proceed": True}
        for _ in range(15):
            out = comp.run(
                proceed=True,
                classification=RateLimitClassification.RATE_LIMIT.value,
                retry_after=None,
            )
        assert out["proceed"] is False

    def test_success_resets_counters(self) -> None:
        from llm_code.engine.components.rate_limiter import RateLimiterComponent

        comp = RateLimiterComponent()
        for _ in range(9):
            comp.run(
                proceed=True,
                classification=RateLimitClassification.RATE_LIMIT.value,
                retry_after=None,
            )
        comp.record_success()
        out = comp.run(
            proceed=True,
            classification=RateLimitClassification.RATE_LIMIT.value,
            retry_after=None,
        )
        assert out["proceed"] is True

    def test_connection_error_backoff(self) -> None:
        from llm_code.engine.components.rate_limiter import RateLimiterComponent

        comp = RateLimiterComponent()
        out = comp.run(
            proceed=True,
            classification=RateLimitClassification.CONNECTION.value,
            retry_after=None,
        )
        assert out["sleep_seconds"] > 0

    def test_timeout_error_backoff(self) -> None:
        from llm_code.engine.components.rate_limiter import RateLimiterComponent

        comp = RateLimiterComponent()
        out = comp.run(
            proceed=True,
            classification=RateLimitClassification.TIMEOUT.value,
            retry_after=None,
        )
        assert out["sleep_seconds"] > 0

    def test_unknown_classification_rejected(self) -> None:
        from llm_code.engine.components.rate_limiter import RateLimiterComponent

        comp = RateLimiterComponent()
        with pytest.raises(ValueError):
            comp.run(
                proceed=True,
                classification="not-a-real-classification",
                retry_after=None,
            )


class TestRateLimiterInPipeline:
    def test_wires_after_denial_tracking(self) -> None:
        from llm_code.engine.components.denial_tracking import (
            DenialTrackingComponent,
        )
        from llm_code.engine.components.rate_limiter import RateLimiterComponent
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("denial", DenialTrackingComponent())
        p.add_component("rate", RateLimiterComponent())
        p.connect("denial.proceed", "rate.proceed")
        assert ("denial", "proceed", "rate", "proceed") in p._connections

    def test_entry_inputs_include_classification(self) -> None:
        from llm_code.engine.components.rate_limiter import RateLimiterComponent
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("rate", RateLimiterComponent())
        assert "classification" in p.inputs()["rate"]

    def test_pipeline_run_passes_through_ok(self) -> None:
        from llm_code.engine.components.rate_limiter import RateLimiterComponent
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("rate", RateLimiterComponent())
        outputs = p.run({
            "rate": {
                "proceed": True,
                "classification": RateLimitClassification.OK.value,
                "retry_after": None,
            },
        })
        assert outputs["rate"]["proceed"] is True

    def test_taxonomy_propagates_to_handler(self) -> None:
        from llm_code.engine.components.rate_limiter import RateLimiterComponent

        taxonomy = ExceptionTaxonomy(rate_limit_types=(TimeoutError,))
        comp = RateLimiterComponent(taxonomy=taxonomy)
        # The handler keeps the taxonomy so classify_from_exception() works.
        assert comp._handler.taxonomy is taxonomy
