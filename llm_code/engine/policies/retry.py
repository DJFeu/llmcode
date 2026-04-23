"""Built-in :class:`RetryPolicy` implementations.

Every policy here is a pure object: it inspects the error, the attempt
counter, and the live :class:`State`, and returns a
:class:`RetryDecision`. None of them mutate state — the Agent owns all
mutations so that policies are swappable without touching the loop.

Spec: docs/superpowers/specs/2026-04-21-llm-code-v12-haystack-borrow-design.md §5.3
Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-agent-loop-control.md Task 3.2
"""
from __future__ import annotations

from typing import Iterable

from llm_code.engine.policies import RetryDecision, RetryPolicy
from llm_code.engine.state import State


class NoRetry:
    """Default policy: never retry. Used when the Agent caller wants the
    pipeline-level rate-limiter + circuit-breaker to handle everything.
    """

    def should_retry(
        self, error: Exception, attempt: int, state: State
    ) -> RetryDecision:
        return RetryDecision(should_retry=False, reason="no-retry policy")


class ExponentialBackoff:
    """Retry transient errors with exponential backoff.

    Delay formula: ``min(base_ms * 2**attempt, cap_ms)``. Only transient
    errors (network timeouts, connection resets) are retried — permanent
    errors (schema violations, permission denials) surface immediately so
    the fallback policy or the model itself can react.

    Transience is determined by :meth:`_is_transient`; extending the set
    of transient types is as simple as subclassing and overriding that
    method.
    """

    def __init__(
        self,
        max_attempts: int = 3,
        base_ms: int = 250,
        cap_ms: int = 8000,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if base_ms < 0 or cap_ms < 0:
            raise ValueError("base_ms and cap_ms must be >= 0")
        self._max = max_attempts
        self._base = base_ms
        self._cap = cap_ms

    def should_retry(
        self, error: Exception, attempt: int, state: State
    ) -> RetryDecision:
        if attempt >= self._max:
            return RetryDecision(
                should_retry=False,
                reason=f"max attempts reached ({self._max})",
            )
        if not self._is_transient(error):
            return RetryDecision(
                should_retry=False,
                reason=f"non-transient error: {type(error).__name__}",
            )
        delay = min(self._base * (2 ** attempt), self._cap)
        return RetryDecision(
            should_retry=True,
            delay_ms=delay,
            reason=f"transient error; backoff {delay}ms",
        )

    @staticmethod
    def _is_transient(error: Exception) -> bool:
        """Classify an error as transient.

        ``httpx`` is an optional dep of the engine subpackage, so we
        import it inside the function; missing ``httpx`` falls back to
        the always-available ``ConnectionResetError`` / ``TimeoutError``
        check. This keeps the engine importable on slim deployments.
        """
        if isinstance(error, (ConnectionResetError, TimeoutError)):
            return True
        try:
            import httpx  # type: ignore

            if isinstance(
                error,
                (
                    httpx.TimeoutException,
                    httpx.ConnectError,
                    httpx.ReadError,
                    httpx.RemoteProtocolError,
                ),
            ):
                return True
        except ImportError:
            pass
        return False


class RetryOnRateLimit:
    """Retry on rate-limit errors, honoring the ``Retry-After`` header.

    Duck-types the error: any exception whose class name contains
    ``"ratelimit"`` (case-insensitive), or any exception with a
    ``retry_after`` / ``retry_after_seconds`` attribute, is treated as
    a rate-limit hit. This avoids coupling the engine to the concrete
    error classes in :mod:`llm_code.api.rate_limiter` and the various
    search-backend modules.
    """

    def __init__(
        self,
        max_attempts: int = 5,
        default_delay_ms: int = 1000,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self._max = max_attempts
        self._default_ms = default_delay_ms

    def should_retry(
        self, error: Exception, attempt: int, state: State
    ) -> RetryDecision:
        if attempt >= self._max:
            return RetryDecision(
                should_retry=False,
                reason=f"max rate-limit retries ({self._max})",
            )
        if not self._is_rate_limit(error):
            return RetryDecision(
                should_retry=False,
                reason=f"not a rate-limit error: {type(error).__name__}",
            )
        delay = self._extract_delay_ms(error)
        return RetryDecision(
            should_retry=True,
            delay_ms=delay,
            reason=f"rate-limit; waiting {delay}ms",
        )

    @staticmethod
    def _is_rate_limit(error: Exception) -> bool:
        name = type(error).__name__.lower()
        if "ratelimit" in name:
            return True
        # The Anthropic / OpenAI SDKs raise specifically-named errors
        # with 429 status codes; we fall back to a status-code check
        # when the class name doesn't make it obvious.
        status = getattr(error, "status_code", None)
        return status == 429

    def _extract_delay_ms(self, error: Exception) -> int:
        # ``Retry-After`` may be exposed as ``retry_after_seconds``
        # (our internal conventions) or the raw header value.
        for attr in ("retry_after_seconds", "retry_after"):
            raw = getattr(error, attr, None)
            if raw is None:
                continue
            try:
                return max(0, int(float(raw) * 1000))
            except (TypeError, ValueError):
                continue
        headers = getattr(error, "headers", None)
        if headers is not None:
            try:
                raw = headers.get("retry-after") or headers.get("Retry-After")
                if raw is not None:
                    return max(0, int(float(raw) * 1000))
            except (AttributeError, TypeError, ValueError):
                pass
        return self._default_ms


class CompositeRetryPolicy:
    """Evaluate child policies in order; first :class:`RetryDecision`
    with ``should_retry=True`` wins.

    This makes it easy to chain e.g. ``RetryOnRateLimit`` (for 429s)
    with ``ExponentialBackoff`` (for generic transient errors): the
    composite tries the specific policy first, then falls through.
    """

    def __init__(self, policies: Iterable[RetryPolicy]) -> None:
        self._policies = tuple(policies)
        if not self._policies:
            raise ValueError("CompositeRetryPolicy requires at least one policy")

    def should_retry(
        self, error: Exception, attempt: int, state: State
    ) -> RetryDecision:
        last: RetryDecision | None = None
        for policy in self._policies:
            decision = policy.should_retry(error, attempt, state)
            if decision.should_retry:
                return decision
            last = decision
        # None matched → surface the last policy's reason so operators
        # can see why the chain bailed out.
        return RetryDecision(
            should_retry=False,
            reason=(last.reason if last else "no sub-policies matched"),
        )


__all__ = [
    "CompositeRetryPolicy",
    "ExponentialBackoff",
    "NoRetry",
    "RetryOnRateLimit",
]
