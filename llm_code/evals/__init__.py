"""llm-code eval framework.

Public surface kept intentionally small — import what you need:

    from llm_code.evals import (
        EvalCase, EvalPolicy, EvalRun, EvalResult,
        check_run,
    )
"""
from llm_code.evals.runner import (
    USUALLY_PASSES_MIN_RATIO,
    Runner,
    RunnerError,
    run_case,
)
from llm_code.evals.types import (
    EvalCase,
    EvalPolicy,
    EvalResult,
    EvalRun,
    JudgeFn,
    check_run,
)

__all__ = (
    "EvalCase",
    "EvalPolicy",
    "EvalResult",
    "EvalRun",
    "JudgeFn",
    "Runner",
    "RunnerError",
    "USUALLY_PASSES_MIN_RATIO",
    "check_run",
    "run_case",
)
