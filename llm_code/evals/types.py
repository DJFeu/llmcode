"""Core types for the llm-code eval framework (C2a — Sprint 2).

An eval is a prompt + assertions pair that exercises a concrete model
(Qwen3.5-Plus, claude-sonnet-4-6, ...) running inside llm-code. Unlike
pytest unit tests, evals tolerate LLM non-determinism via two
:class:`EvalPolicy` bands:

    * ``ALWAYS_PASSES``   — must pass every time; gates CI.
    * ``USUALLY_PASSES``  — nightly only; tolerates flakiness, tracks
      pass rate across multiple runs instead of demanding 4/4.

The types in this module are pure data. Runner, pytest integration,
and concrete cases live in sibling modules.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable


class EvalPolicy(Enum):
    """How strict we are about an eval's outcome."""
    ALWAYS_PASSES = "always_passes"
    USUALLY_PASSES = "usually_passes"

    @classmethod
    def from_string(cls, value: str) -> "EvalPolicy":
        for member in cls:
            if member.value == value.lower():
                return member
        raise ValueError(
            f"unknown EvalPolicy {value!r}; expected one of "
            f"{[m.value for m in cls]}"
        )


@dataclass(frozen=True)
class EvalRun:
    """Result of a single runner invocation against one case."""
    case_id: str
    final_text: str
    tool_calls: tuple[str, ...]
    duration_seconds: float
    error: str | None = None

    @property
    def is_success(self) -> bool:
        return self.error is None


# Judge functions can encode assertions that are too complex for the
# declarative ``expected_*`` fields — e.g. "the output is valid JSON".
JudgeFn = Callable[[EvalRun], bool]


@dataclass(frozen=True)
class EvalCase:
    """Declarative description of one eval."""
    id: str
    prompt: str
    policy: EvalPolicy
    # Every expected tool must appear at least once in ``run.tool_calls``.
    expected_tools: tuple[str, ...] = ()
    # Every substring must appear in ``run.final_text``.
    expected_text_contains: tuple[str, ...] = ()
    # Custom judge runs after the declarative checks pass. When present,
    # its return value is the authority on pass/fail for that run.
    judge_fn: JudgeFn | None = None
    # Runner honours this as a hard wall-clock cap.
    timeout_seconds: float = 60.0
    # Free-form tags for filtering (e.g. ``("qwen", "xml-tools")``).
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvalResult:
    """Aggregate outcome across N runs of the same case."""
    case: EvalCase
    runs: tuple[EvalRun, ...]
    passed: bool
    passing_runs: int
    total_runs: int
    reason: str = ""

    @property
    def pass_rate(self) -> float:
        if self.total_runs <= 0:
            return 0.0
        return self.passing_runs / self.total_runs


def check_run(case: EvalCase, run: EvalRun) -> bool:
    """Evaluate one :class:`EvalRun` against a :class:`EvalCase`.

    Order:

        1. If the run errored, fail fast.
        2. Declarative assertions (``expected_tools`` first, then
           ``expected_text_contains``). Each element must be satisfied.
        3. If everything declarative passed and a ``judge_fn`` is set,
           its return value becomes the final verdict.

    The judge never runs when the declarative layer already failed —
    it's an extension point, not a bypass.
    """
    if run.error is not None:
        return False

    if case.expected_tools:
        called = set(run.tool_calls)
        if not all(tool in called for tool in case.expected_tools):
            return False

    if case.expected_text_contains:
        if not all(sub in run.final_text for sub in case.expected_text_contains):
            return False

    if case.judge_fn is not None:
        return bool(case.judge_fn(run))

    return True
