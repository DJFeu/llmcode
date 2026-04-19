"""Tests for the eval runner + best-of-N aggregation (C2b).

A ``Runner`` is a callable that takes a prompt and returns an
:class:`EvalRun`. ``run_case`` invokes the runner ``times`` times and
aggregates the outcomes against the case's policy.
"""
from __future__ import annotations

from typing import Callable

import pytest

from llm_code.evals import EvalCase, EvalPolicy, EvalRun
from llm_code.evals.runner import (
    USUALLY_PASSES_MIN_RATIO,
    RunnerError,
    run_case,
)


def _make_runner(outputs: list[EvalRun]) -> Callable[..., EvalRun]:
    """Runner that returns ``outputs[n]`` on the n-th call."""
    idx = {"i": 0}

    def runner(prompt: str, timeout: float) -> EvalRun:  # noqa: ARG001
        i = idx["i"]
        idx["i"] += 1
        return outputs[i]

    return runner


def _mk_case(policy: EvalPolicy, **kwargs) -> EvalCase:
    defaults = dict(
        id="c", prompt="say ok", policy=policy,
        expected_text_contains=("ok",),
    )
    defaults.update(kwargs)
    return EvalCase(**defaults)


def _mk_run(text: str = "ok", *, error: str | None = None) -> EvalRun:
    return EvalRun(
        case_id="c", final_text=text, tool_calls=(),
        duration_seconds=0.01, error=error,
    )


# ---------- ALWAYS_PASSES: every run must pass ----------


class TestAlwaysPasses:
    def test_all_four_pass(self) -> None:
        runner = _make_runner([_mk_run()] * 4)
        result = run_case(_mk_case(EvalPolicy.ALWAYS_PASSES), runner, times=4)
        assert result.passed is True
        assert result.passing_runs == 4
        assert result.total_runs == 4

    def test_one_failure_fails_overall(self) -> None:
        runner = _make_runner(
            [_mk_run(), _mk_run(), _mk_run("nope"), _mk_run()]
        )
        result = run_case(_mk_case(EvalPolicy.ALWAYS_PASSES), runner, times=4)
        assert result.passed is False
        assert result.passing_runs == 3
        assert "ALWAYS_PASSES" in result.reason

    def test_runner_exception_becomes_failed_run(self) -> None:
        def runner(prompt: str, timeout: float):  # noqa: ARG001
            raise RuntimeError("boom")

        result = run_case(_mk_case(EvalPolicy.ALWAYS_PASSES), runner, times=2)
        assert result.passed is False
        assert result.passing_runs == 0
        # The run was captured, not re-raised
        assert all(r.error for r in result.runs)


# ---------- USUALLY_PASSES: tolerates flakes up to the ratio ----------


class TestUsuallyPasses:
    def test_three_of_four_passes(self) -> None:
        runner = _make_runner(
            [_mk_run(), _mk_run(), _mk_run("nope"), _mk_run()]
        )
        result = run_case(_mk_case(EvalPolicy.USUALLY_PASSES), runner, times=4)
        assert result.passing_runs == 3
        assert result.total_runs == 4
        # 0.75 >= USUALLY_PASSES_MIN_RATIO (0.75 default)
        assert result.passed is True

    def test_two_of_four_fails(self) -> None:
        runner = _make_runner(
            [_mk_run(), _mk_run("nope"), _mk_run("nope"), _mk_run()]
        )
        result = run_case(_mk_case(EvalPolicy.USUALLY_PASSES), runner, times=4)
        assert result.passing_runs == 2
        assert result.passed is False

    def test_custom_min_ratio(self) -> None:
        runner = _make_runner(
            [_mk_run(), _mk_run("nope"), _mk_run(), _mk_run()]
        )
        # 3/4 = 0.75 but we require 0.80
        result = run_case(
            _mk_case(EvalPolicy.USUALLY_PASSES),
            runner, times=4,
            usually_passes_min_ratio=0.80,
        )
        assert result.passed is False


# ---------- Times parameter ----------


class TestRunCount:
    def test_times_1_single_run(self) -> None:
        runner = _make_runner([_mk_run()])
        result = run_case(_mk_case(EvalPolicy.ALWAYS_PASSES), runner, times=1)
        assert result.total_runs == 1

    def test_invalid_times_raises(self) -> None:
        with pytest.raises(ValueError):
            run_case(_mk_case(EvalPolicy.ALWAYS_PASSES),
                     _make_runner([_mk_run()]), times=0)


# ---------- Runner signature ----------


class TestRunnerSignature:
    def test_runner_sees_prompt_and_timeout(self) -> None:
        seen: list[tuple[str, float]] = []

        def runner(prompt: str, timeout: float) -> EvalRun:
            seen.append((prompt, timeout))
            return _mk_run()

        run_case(
            _mk_case(EvalPolicy.ALWAYS_PASSES, prompt="hi", timeout_seconds=7.0),
            runner,
            times=2,
        )
        assert seen == [("hi", 7.0), ("hi", 7.0)]


# ---------- RunnerError wrapping ----------


class TestRunnerError:
    def test_runtime_error_captured_with_message(self) -> None:
        def runner(prompt: str, timeout: float):  # noqa: ARG001
            raise RuntimeError("provider died")

        result = run_case(_mk_case(EvalPolicy.ALWAYS_PASSES), runner, times=1)
        assert result.runs[0].error is not None
        assert "provider died" in result.runs[0].error

    def test_runner_error_typed_exception_preserved(self) -> None:
        def runner(prompt: str, timeout: float):  # noqa: ARG001
            raise RunnerError("custom error text")

        result = run_case(_mk_case(EvalPolicy.ALWAYS_PASSES), runner, times=1)
        assert "custom error text" in result.runs[0].error


# ---------- Pass rate surface ----------


class TestPassRate:
    def test_pass_rate_reported(self) -> None:
        runner = _make_runner(
            [_mk_run(), _mk_run("nope"), _mk_run(), _mk_run()]
        )
        result = run_case(_mk_case(EvalPolicy.USUALLY_PASSES), runner, times=4)
        assert result.pass_rate == 0.75

    def test_threshold_constant(self) -> None:
        # The documented default mirrors qwen-code / Gemini CLI practice.
        assert USUALLY_PASSES_MIN_RATIO == 0.75
