"""Eval runner + best-of-N aggregation (C2b — Sprint 2).

Invokes a :class:`Runner` ``times`` times and decides pass/fail against
the case's :class:`EvalPolicy`. LLM non-determinism is handled via
two bands:

    * ``ALWAYS_PASSES``  — every run must succeed. Suitable for CI.
    * ``USUALLY_PASSES`` — tolerates flakes up to
      ``USUALLY_PASSES_MIN_RATIO`` (default 0.75 — mirrors qwen-code
      and Gemini CLI's "trustworthy evals" band).

A ``Runner`` is any callable ``(prompt: str, timeout: float) -> EvalRun``.
Production runners talk to a real provider through llm-code's runtime;
the test suite uses small closures so C2b stays self-contained.
"""
from __future__ import annotations

import time
from typing import Callable

from llm_code.evals.types import (
    EvalCase,
    EvalPolicy,
    EvalResult,
    EvalRun,
    check_run,
)

# Fraction of runs that must pass for USUALLY_PASSES to count as green.
USUALLY_PASSES_MIN_RATIO: float = 0.75


Runner = Callable[[str, float], EvalRun]


class RunnerError(Exception):
    """Raised by a runner when it cannot produce an :class:`EvalRun`.

    The runner harness catches any ``Exception`` and wraps it in an
    ``EvalRun(error=...)`` so one flaky call doesn't poison the
    aggregate. This subclass exists purely to give production runners
    a typed error to raise when they want to signal "something
    genuinely broke" vs. the model producing bad output.
    """


def run_case(
    case: EvalCase,
    runner: Runner,
    *,
    times: int = 4,
    usually_passes_min_ratio: float = USUALLY_PASSES_MIN_RATIO,
) -> EvalResult:
    """Invoke ``runner`` ``times`` times and aggregate into an :class:`EvalResult`.

    Each call is protected: runner exceptions are captured as
    ``EvalRun(error=...)`` so one dead call never prevents the other
    runs from reporting. The outcome is scored per
    :func:`check_run`; pass/fail then rolls up under the case's
    :class:`EvalPolicy`.
    """
    if times < 1:
        raise ValueError(f"times must be >= 1 (got {times})")

    runs: list[EvalRun] = []
    for _ in range(times):
        start = time.monotonic()
        try:
            run = runner(case.prompt, case.timeout_seconds)
        except Exception as exc:  # noqa: BLE001 — harness must not leak
            run = EvalRun(
                case_id=case.id,
                final_text="",
                tool_calls=(),
                duration_seconds=time.monotonic() - start,
                error=f"{type(exc).__name__}: {exc}",
            )
        runs.append(run)

    passing = sum(1 for r in runs if check_run(case, r))
    total = len(runs)

    passed, reason = _verdict(case.policy, passing, total, usually_passes_min_ratio)

    return EvalResult(
        case=case,
        runs=tuple(runs),
        passed=passed,
        passing_runs=passing,
        total_runs=total,
        reason=reason,
    )


def _verdict(
    policy: EvalPolicy,
    passing: int,
    total: int,
    min_ratio: float,
) -> tuple[bool, str]:
    if total <= 0:
        return False, "no runs produced"

    if policy is EvalPolicy.ALWAYS_PASSES:
        if passing == total:
            return True, f"ALWAYS_PASSES: {passing}/{total}"
        return False, f"ALWAYS_PASSES required every run to pass, got {passing}/{total}"

    ratio = passing / total
    if ratio >= min_ratio:
        return True, f"USUALLY_PASSES: {passing}/{total} (ratio {ratio:.2f} >= {min_ratio:.2f})"
    return False, (
        f"USUALLY_PASSES threshold missed: {passing}/{total} "
        f"(ratio {ratio:.2f} < {min_ratio:.2f})"
    )
