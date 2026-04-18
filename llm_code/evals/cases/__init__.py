"""Built-in eval case catalogue.

Each sub-module defines a curated tuple of :class:`EvalCase` targeting
one model family or capability. The cases are data only — wire them to
a real llm-code runner via ``run_case`` in a nightly workflow.

Usage::

    from llm_code.evals.cases import qwen
    for case in qwen.CASES:
        result = run_case(case, my_runner, times=4)
"""
from llm_code.evals.cases import qwen

__all__ = ("qwen",)
