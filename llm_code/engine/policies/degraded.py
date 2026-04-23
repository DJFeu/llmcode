"""Built-in :class:`DegradedModePolicy` implementations.

Degraded mode is a capability downgrade: the agent keeps running but
the set of tools it can invoke shrinks. Two triggers ship today:

- :class:`ConsecutiveFailureDegraded` — N tool calls in a row fail.
  The agent is clearly stuck; drop to read-only so it can investigate
  without making things worse.
- :class:`BudgetDegraded` — token or wall-clock budget is nearly
  exhausted. Switch to read-only + ask the model to summarise so we
  land on a useful final answer before hitting the hard cap.

Spec: docs/superpowers/specs/2026-04-21-llm-code-v12-haystack-borrow-design.md §5.3
Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-agent-loop-control.md Task 3.4
"""
from __future__ import annotations

from typing import Callable, Iterable

from llm_code.engine.policies import DegradedDecision
from llm_code.engine.state import State

# Canonical read-only tool set used by every degraded policy. Kept as a
# module constant (not a class attribute) so users can import and
# extend it without subclassing. ``frozenset`` guarantees no mutation
# across the process lifetime.
READ_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "grep_search",
        "glob_search",
        "lsp_hover",
        "lsp_document_symbol",
        "web_search",
        "web_fetch",
    }
)


class NoDegraded:
    """Default: never degrade. Useful in environments where every tool
    call is cheap and determinism matters more than graceful failure.
    """

    def check(self, state: State) -> DegradedDecision:
        return DegradedDecision(should_degrade=False)


class ConsecutiveFailureDegraded:
    """Trip degraded mode after N consecutive tool failures.

    ``tool_results`` is expected to be a list of objects with an
    ``is_error`` bool attribute (our :class:`~llm_code.engine.agent.AgentError`
    satisfies this). Falling back to ``dict.get("is_error")`` keeps the
    policy usable with plain-dict stubs in unit tests.
    """

    def __init__(
        self,
        threshold: int = 3,
        allowed_tools: frozenset[str] | None = None,
    ) -> None:
        if threshold < 1:
            raise ValueError("threshold must be >= 1")
        self._threshold = threshold
        self._allowed = allowed_tools if allowed_tools is not None else READ_ONLY_TOOLS

    def check(self, state: State) -> DegradedDecision:
        results = list(state.get("tool_results", []))
        if len(results) < self._threshold:
            return DegradedDecision(should_degrade=False)
        window = results[-self._threshold:]
        if all(_is_error(r) for r in window):
            return DegradedDecision(
                should_degrade=True,
                allowed_tools=self._allowed,
                reason=f"{self._threshold} consecutive tool failures",
            )
        return DegradedDecision(should_degrade=False)


class BudgetDegraded:
    """Trip degraded mode when a budget is ``>= threshold`` exhausted.

    The caller supplies a ``usage_fn`` that returns the current usage
    ratio in ``[0.0, 1.0]``. This indirection keeps the policy decoupled
    from our token-accounting module so it can read from any source
    (tokens, wall-clock, dollars, …).
    """

    def __init__(
        self,
        usage_fn: Callable[[State], float],
        threshold: float = 0.8,
        allowed_tools: frozenset[str] | None = None,
    ) -> None:
        if not 0.0 < threshold <= 1.0:
            raise ValueError("threshold must be in (0, 1]")
        self._usage = usage_fn
        self._threshold = threshold
        self._allowed = allowed_tools if allowed_tools is not None else READ_ONLY_TOOLS

    def check(self, state: State) -> DegradedDecision:
        try:
            used = float(self._usage(state))
        except Exception as exc:  # noqa: BLE001 - defensive
            # A broken budget hook must not crash the agent; surface a
            # negative decision and let the loop continue.
            return DegradedDecision(
                should_degrade=False,
                reason=f"usage_fn raised: {exc}",
            )
        if used >= self._threshold:
            return DegradedDecision(
                should_degrade=True,
                allowed_tools=self._allowed,
                reason=(
                    f"budget {used:.0%} used (threshold {self._threshold:.0%})"
                ),
            )
        return DegradedDecision(should_degrade=False)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _is_error(result) -> bool:
    """Best-effort error detection for a tool result.

    We support two shapes:

    1. Objects with an ``is_error`` bool attribute — canonical
       :class:`AgentError` from the engine.
    2. Mappings with an ``"is_error"`` key — used by lightweight test
       stubs to avoid importing the engine dataclasses.
    """
    if hasattr(result, "is_error"):
        return bool(getattr(result, "is_error"))
    if isinstance(result, dict):
        return bool(result.get("is_error"))
    return False


def all_read_only(tools: Iterable[str]) -> bool:
    """Helper: return True iff every tool name is in :data:`READ_ONLY_TOOLS`.

    Useful for tests that need to verify a downgrade happened without
    knowing the exact allowed_tools value.
    """
    return all(t in READ_ONLY_TOOLS for t in tools)


__all__ = [
    "BudgetDegraded",
    "ConsecutiveFailureDegraded",
    "NoDegraded",
    "READ_ONLY_TOOLS",
    "all_read_only",
]
