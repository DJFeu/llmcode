"""RateLimiterComponent — rate-limit policy as a Pipeline stage.

Wraps :class:`llm_code.api.rate_limiter.RateLimitHandler` so the
pipeline can decide, given an upstream classification of the most recent
attempt, whether to continue and how long to sleep before retrying.

Design notes
------------
- The Component takes ``classification`` as a string (the enum ``.value``)
  because Pipelines pass primitives through Socket types. Unknown strings
  raise :class:`ValueError` at run time so a typo becomes loud quickly.
- Counters live on the underlying handler, so successive ``.run()`` calls
  on the same instance accumulate attempts — matching how retry loops
  behave in production.
- ``record_success()`` is exposed so the Agent loop (M3) can reset the
  handler after a successful provider call.

Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-pipeline-dag.md Task 2.7 Step 2
"""
from __future__ import annotations

from typing import Any

from llm_code.api.rate_limiter import (
    ExceptionTaxonomy,
    RateLimitClassification,
    RateLimitHandler,
    RequestKind,
    should_retry,
)
from llm_code.engine.component import component, output_types


def _coerce_classification(value: str) -> RateLimitClassification:
    """Turn a socket-friendly string into the strongly-typed enum.

    Accepts the enum ``.value`` strings only (``"ok"``, ``"rate_limit"``…).
    Any other string raises ``ValueError`` so misconfigured Pipelines fail
    fast instead of silently skipping retry logic.
    """
    try:
        return RateLimitClassification(value)
    except ValueError as exc:
        valid = ", ".join(sorted(c.value for c in RateLimitClassification))
        raise ValueError(
            f"unknown rate-limit classification {value!r} — expected one of: {valid}",
        ) from exc


@component
@output_types(proceed=bool, sleep_seconds=float, reason=str)
class RateLimiterComponent:
    """Stateful retry-policy gate.

    Args:
        request_kind: Whether the wrapped request blocks user input
            (foreground) or runs async (background). Controls the
            retry budget per classification.
        taxonomy: Provider-specific exception classes. Kept on the
            handler for callers that later need the classifier.
    """

    def __init__(
        self,
        *,
        request_kind: RequestKind = RequestKind.FOREGROUND,
        taxonomy: ExceptionTaxonomy | None = None,
    ) -> None:
        self._handler = RateLimitHandler(
            request_kind=request_kind,
            taxonomy=taxonomy,
        )

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    def record_success(self) -> None:
        """Reset handler counters — called by the Agent on success."""
        self._handler.record_success()

    def run(
        self,
        proceed: bool,
        classification: str,
        retry_after: float | None = None,
    ) -> dict[str, Any]:
        """Decide whether to proceed with another attempt after a failure."""
        if not proceed:
            return {"proceed": False, "sleep_seconds": 0.0, "reason": "upstream denied"}

        cls = _coerce_classification(classification)
        # OK classification is not a retry event; short-circuit without
        # touching counters.
        if cls is RateLimitClassification.OK:
            return {"proceed": True, "sleep_seconds": 0.0, "reason": ""}

        decision = should_retry(
            cls,
            attempt=self._handler.attempt,
            overload_attempt=self._handler.overload_attempt,
            request_kind=self._handler.request_kind,
            retry_after=retry_after,
        )
        # Mirror what `RateLimitHandler.on_exception` does post-decision
        # so subsequent `.run()` calls see the incremented counter.
        if cls is RateLimitClassification.OVERLOAD:
            self._handler.overload_attempt += 1
        else:
            self._handler.attempt += 1
        return {
            "proceed": decision.retry,
            "sleep_seconds": float(decision.sleep_seconds),
            "reason": decision.reason,
        }
