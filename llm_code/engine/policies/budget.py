"""RetryBudget — hard cap on total retries in one Agent run.

The budget is a cross-cutting guard that sits underneath every other
policy. Without it, two adversarially-configured policies (RetryA says
retry, FallbackB says swap-then-retry, RetryA says retry on the new
tool, …) could loop forever. The budget is a plain counter: when it
hits ``max_total_retries``, no further retries or fallback-triggered
retries are allowed for the rest of the run.

Spec: docs/superpowers/specs/2026-04-21-llm-code-v12-haystack-borrow-design.md §5.3
Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-agent-loop-control.md Task 3.6
"""
from __future__ import annotations


class RetryBudget:
    """Simple retry counter; one instance per :class:`Agent.run` call.

    The budget is *not* thread-safe: the Agent loop is sequential, and
    the async variant (M5) uses a per-task budget anyway. Callers that
    want to share a budget across parallel sub-agents should wrap it
    themselves.
    """

    def __init__(self, max_total_retries: int = 20) -> None:
        if max_total_retries < 0:
            raise ValueError("max_total_retries must be >= 0")
        self._max = max_total_retries
        self._used = 0

    @property
    def max_total_retries(self) -> int:
        return self._max

    @property
    def used(self) -> int:
        return self._used

    @property
    def remaining(self) -> int:
        return max(0, self._max - self._used)

    def can_retry(self) -> bool:
        """Return ``True`` if one more retry is allowed."""
        return self._used < self._max

    def consume(self) -> None:
        """Record one retry against the budget.

        Raises:
            RuntimeError: if called after the budget is exhausted. This
                is a defensive check — every caller should test
                :meth:`can_retry` first. The exception makes bugs in
                the Agent loop surface during tests instead of silently
                overspending.
        """
        if self._used >= self._max:
            raise RuntimeError(
                f"retry budget exhausted ({self._used}/{self._max})"
            )
        self._used += 1

    def reset(self) -> None:
        """Reset the counter; used by tests or when reusing a budget
        across multiple Agent runs (not recommended in production).
        """
        self._used = 0


__all__ = ["RetryBudget"]
