"""Shared rate-limit retry policy (C3 — Sprint 2).

The existing provider code (``openai_compat.py`` / ``anthropic_provider.py``)
owns its own ``_post_with_retry`` loop. This module factors out the
*policy* decisions so both providers share one implementation:

    * :func:`classify_exception` — map a raised exception onto a track
      (rate-limit / overload / connection / timeout / permanent / unknown)
    * :func:`next_backoff`       — compute the sleep for the next retry
    * :func:`should_retry`       — given the attempt counters and the
      request kind, decide whether to loop again
    * :class:`RateLimitHandler`  — stateful orchestrator that wraps the
      three functions and fires an optional heartbeat callback on the
      long overload track

Provider loops keep their own httpx calls — they just consult these
functions for the decisions. The change is additive and backward
compatible.

Policy summary (mirrors Claude Code's ``withRetry.ts``):

    Request kind         | 429 rate-limit   | 529 overload       | Connection/Timeout
    ---------------------|------------------|--------------------|---------------------
    FOREGROUND (user)    | 10 retries max   | persistent, max 5m | 3 retries
    BACKGROUND (async)   | bail immediately | 1 retry, max 5m    | 1 retry

A heartbeat callback fires at most once per ``heartbeat_interval``
seconds while the handler is on the OVERLOAD track, so the outer
process (REPL, orchestrator) can keep a "still retrying..." line on
screen instead of going silent for minutes.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Sequence, TypeVar


# ── Enumerations ──────────────────────────────────────────────────────


class RateLimitClassification(Enum):
    """Which retry track applies to this failure."""
    OK = "ok"                   # not an error at all
    RATE_LIMIT = "rate_limit"   # HTTP 429 / provider rate-limit exc
    OVERLOAD = "overload"       # HTTP 529 / provider overload exc
    CONNECTION = "connection"   # socket / DNS / reset
    TIMEOUT = "timeout"         # read / write / connect timeout
    PERMANENT = "permanent"     # auth / model-not-found / 4xx non-429
    UNKNOWN = "unknown"         # exception we don't recognise


class RequestKind(Enum):
    """Whether the call blocks user input (FG) or runs async (BG)."""
    FOREGROUND = "foreground"
    BACKGROUND = "background"


# ── Constants ────────────────────────────────────────────────────────

# Upper bound on how long the persistent overload track can sleep.
_PERSISTENT_MAX_BACKOFF_SECONDS: float = 300.0

# Long-backoff schedule for HTTP 529. Beyond the final entry the loop
# stays on the persistent max.
_OVERLOAD_BACKOFF_SCHEDULE: tuple[float, ...] = (30.0, 60.0, 120.0)

# Exponential bases.
_RATE_LIMIT_BASE_SECONDS: float = 0.5
_CONNECTION_BASE_SECONDS: float = 1.0

# Per-kind retry budgets by classification.
_FG_RATE_LIMIT_MAX: int = 10
_FG_CONNECTION_MAX: int = 3
_BG_RATE_LIMIT_MAX: int = 0  # bail immediately
_BG_CONNECTION_MAX: int = 1
_FG_OVERLOAD_MAX: int = 20   # persistent — "long enough to survive a blip"
_BG_OVERLOAD_MAX: int = 1


# ── Exception taxonomy ────────────────────────────────────────────────


@dataclass(frozen=True)
class ExceptionTaxonomy:
    """Provider-specific exception class lists."""
    rate_limit_types: tuple[type[BaseException], ...] = ()
    overload_types: tuple[type[BaseException], ...] = ()
    connection_types: tuple[type[BaseException], ...] = ()
    timeout_types: tuple[type[BaseException], ...] = ()
    permanent_types: tuple[type[BaseException], ...] = ()


# ── Pure classification / backoff / retry-decision ────────────────────


def classify_exception(
    exc: BaseException,
    *,
    rate_limit_types: Sequence[type[BaseException]] = (),
    overload_types: Sequence[type[BaseException]] = (),
    connection_types: Sequence[type[BaseException]] = (),
    timeout_types: Sequence[type[BaseException]] = (),
    permanent_types: Sequence[type[BaseException]] = (),
) -> RateLimitClassification:
    """Map ``exc`` onto a :class:`RateLimitClassification`.

    Permanent errors are checked first because they otherwise get
    shadowed by the generic ``Exception`` fallback. Unknown exceptions
    fall through to ``UNKNOWN`` — callers should treat those as
    retryable-once for foreground requests so transient bugs don't
    cascade into full runtime failures.
    """
    if isinstance(exc, tuple(permanent_types)):
        return RateLimitClassification.PERMANENT
    if isinstance(exc, tuple(rate_limit_types)):
        return RateLimitClassification.RATE_LIMIT
    if isinstance(exc, tuple(overload_types)):
        return RateLimitClassification.OVERLOAD
    if isinstance(exc, tuple(connection_types)):
        return RateLimitClassification.CONNECTION
    if isinstance(exc, tuple(timeout_types)):
        return RateLimitClassification.TIMEOUT
    return RateLimitClassification.UNKNOWN


def next_backoff(
    classification: RateLimitClassification,
    *,
    attempt: int,
    overload_attempt: int,
    retry_after: float | None = None,
) -> float:
    """Compute the number of seconds to sleep before the next retry.

    Honours a provider-supplied ``retry_after`` when present on the
    rate-limit track. For overload track the schedule is fixed; beyond
    the schedule the loop stays at the persistent maximum so unresolved
    server overload doesn't drift to infinity.
    """
    if classification is RateLimitClassification.RATE_LIMIT:
        if retry_after is not None and retry_after > 0:
            return float(retry_after)
        return min(_RATE_LIMIT_BASE_SECONDS * (2 ** attempt), _PERSISTENT_MAX_BACKOFF_SECONDS)
    if classification is RateLimitClassification.OVERLOAD:
        if overload_attempt < len(_OVERLOAD_BACKOFF_SCHEDULE):
            return _OVERLOAD_BACKOFF_SCHEDULE[overload_attempt]
        return _PERSISTENT_MAX_BACKOFF_SECONDS
    if classification in (RateLimitClassification.CONNECTION, RateLimitClassification.TIMEOUT):
        return min(_CONNECTION_BASE_SECONDS * (2 ** attempt), _PERSISTENT_MAX_BACKOFF_SECONDS)
    return 0.0


@dataclass(frozen=True)
class RateLimitDecision:
    """Outcome of consulting the retry policy for a single failure."""
    retry: bool
    sleep_seconds: float
    classification: RateLimitClassification
    reason: str = ""


def should_retry(
    classification: RateLimitClassification,
    *,
    attempt: int,
    overload_attempt: int,
    request_kind: RequestKind,
    retry_after: float | None = None,
) -> RateLimitDecision:
    """Decide whether to loop again for ``classification`` at these counters."""
    if classification is RateLimitClassification.PERMANENT:
        return RateLimitDecision(
            retry=False, sleep_seconds=0.0, classification=classification,
            reason="permanent error (auth / not found)",
        )

    is_fg = request_kind is RequestKind.FOREGROUND

    if classification is RateLimitClassification.RATE_LIMIT:
        budget = _FG_RATE_LIMIT_MAX if is_fg else _BG_RATE_LIMIT_MAX
        if attempt >= budget:
            return RateLimitDecision(
                retry=False, sleep_seconds=0.0, classification=classification,
                reason=f"rate-limit budget exhausted (attempt={attempt}/{budget})",
            )
        return RateLimitDecision(
            retry=True,
            sleep_seconds=next_backoff(
                classification, attempt=attempt,
                overload_attempt=overload_attempt,
                retry_after=retry_after,
            ),
            classification=classification,
            reason="rate-limit — exponential backoff" if retry_after is None
                   else f"rate-limit — honouring Retry-After={retry_after}s",
        )

    if classification is RateLimitClassification.OVERLOAD:
        budget = _FG_OVERLOAD_MAX if is_fg else _BG_OVERLOAD_MAX
        if overload_attempt >= budget:
            return RateLimitDecision(
                retry=False, sleep_seconds=0.0, classification=classification,
                reason=f"overload budget exhausted (overload_attempt={overload_attempt}/{budget})",
            )
        return RateLimitDecision(
            retry=True,
            sleep_seconds=next_backoff(
                classification, attempt=attempt,
                overload_attempt=overload_attempt,
                retry_after=retry_after,
            ),
            classification=classification,
            reason="server overload — persistent backoff track",
        )

    if classification in (RateLimitClassification.CONNECTION, RateLimitClassification.TIMEOUT):
        budget = _FG_CONNECTION_MAX if is_fg else _BG_CONNECTION_MAX
        if attempt >= budget:
            return RateLimitDecision(
                retry=False, sleep_seconds=0.0, classification=classification,
                reason=f"{classification.value} budget exhausted",
            )
        return RateLimitDecision(
            retry=True,
            sleep_seconds=next_backoff(
                classification, attempt=attempt,
                overload_attempt=overload_attempt,
                retry_after=retry_after,
            ),
            classification=classification,
            reason=f"{classification.value} — exponential backoff",
        )

    # UNKNOWN — retry exactly once on foreground.
    if is_fg and attempt == 0:
        return RateLimitDecision(
            retry=True,
            sleep_seconds=_CONNECTION_BASE_SECONDS,
            classification=classification,
            reason="unknown exception — retry once",
        )
    return RateLimitDecision(
        retry=False, sleep_seconds=0.0, classification=classification,
        reason="unknown exception — not retrying",
    )


# ── Stateful orchestrator ─────────────────────────────────────────────


HeartbeatCallback = Callable[[dict[str, Any]], None]


@dataclass
class RateLimitHandler:
    """Stateful retry policy for a single logical request.

    Usage::

        handler = RateLimitHandler(
            request_kind=RequestKind.FOREGROUND,
            heartbeat_interval=30.0,
            on_heartbeat=lambda info: logger.info("retrying...", extra=info),
            taxonomy=taxonomy,
        )

        while True:
            try:
                response = await call_provider()
                handler.record_success()
                return response
            except Exception as exc:
                decision = handler.on_exception(exc)
                if not decision.retry:
                    raise
                await asyncio.sleep(decision.sleep_seconds)

    The handler tracks separate counters for the RATE_LIMIT/CONNECTION
    track (``attempt``) and the OVERLOAD track (``overload_attempt``)
    so one doesn't eat the other's budget.
    """

    request_kind: RequestKind = RequestKind.FOREGROUND
    heartbeat_interval: float = 30.0
    on_heartbeat: HeartbeatCallback | None = None
    taxonomy: ExceptionTaxonomy | None = None
    attempt: int = 0
    overload_attempt: int = 0
    _last_heartbeat_at: float = field(default=0.0, init=False, repr=False)

    # ------------------------------------------------------------------

    def record_success(self) -> None:
        """Reset counters after a successful request."""
        self.attempt = 0
        self.overload_attempt = 0
        self._last_heartbeat_at = 0.0

    def on_exception(
        self,
        exc: BaseException,
        *,
        rate_limit_types: Sequence[type[BaseException]] = (),
        overload_types: Sequence[type[BaseException]] = (),
        connection_types: Sequence[type[BaseException]] = (),
        timeout_types: Sequence[type[BaseException]] = (),
        permanent_types: Sequence[type[BaseException]] = (),
    ) -> RateLimitDecision:
        """Classify ``exc`` and return the retry decision.

        Per-call type lists override ``self.taxonomy`` when provided —
        lets simple tests stay self-contained while providers can ship
        a default taxonomy at construction time.
        """
        tax = self.taxonomy
        classification = classify_exception(
            exc,
            rate_limit_types=rate_limit_types or (tax.rate_limit_types if tax else ()),
            overload_types=overload_types or (tax.overload_types if tax else ()),
            connection_types=connection_types or (tax.connection_types if tax else ()),
            timeout_types=timeout_types or (tax.timeout_types if tax else ()),
            permanent_types=permanent_types or (tax.permanent_types if tax else ()),
        )

        retry_after = getattr(exc, "retry_after", None)

        decision = should_retry(
            classification,
            attempt=self.attempt,
            overload_attempt=self.overload_attempt,
            request_kind=self.request_kind,
            retry_after=retry_after,
        )

        # Increment counters before the caller sleeps so the next call
        # sees the bumped state. We increment even on `retry=False` so
        # the handler reflects how many failures actually happened.
        if classification is RateLimitClassification.OVERLOAD:
            self.overload_attempt += 1
            self._maybe_emit_heartbeat(classification, decision)
        else:
            self.attempt += 1

        return decision

    # ------------------------------------------------------------------

    def _maybe_emit_heartbeat(
        self,
        classification: RateLimitClassification,
        decision: RateLimitDecision,
    ) -> None:
        if self.on_heartbeat is None:
            return
        now = time.monotonic()
        if now - self._last_heartbeat_at < self.heartbeat_interval:
            return
        self._last_heartbeat_at = now
        try:
            self.on_heartbeat({
                "classification": classification.value,
                "attempt": self.attempt,
                "overload_attempt": self.overload_attempt,
                "sleep_seconds": decision.sleep_seconds,
                "reason": decision.reason,
                "request_kind": self.request_kind.value,
            })
        except Exception:
            # Heartbeat callbacks must never crash the retry loop.
            pass

    # ------------------------------------------------------------------
    # M5 — async acquire semantics
    # ------------------------------------------------------------------

    async def acquire_async(
        self,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Block asynchronously until the handler's state permits a new call.

        In v12 the retry loop is driven by :func:`run_with_rate_limit`;
        this helper exists for callers that want to gate a *new* request
        against the same persistent counters (e.g. a speculative prefetch
        that must respect an ongoing 529 backoff). When counters are
        zero the coroutine returns immediately.

        We derive the sleep from the same schedule used on the OVERLOAD
        track — the rationale is that overload is the only classification
        that should delay an *unrelated* new request. Rate-limit on a
        single request doesn't spill across requests.
        """
        if self.overload_attempt == 0:
            return
        # Reuse the same schedule as next_backoff(OVERLOAD).
        idx = min(self.overload_attempt - 1, len(_OVERLOAD_BACKOFF_SCHEDULE) - 1)
        wait = _OVERLOAD_BACKOFF_SCHEDULE[idx]
        await sleep(min(wait, _PERSISTENT_MAX_BACKOFF_SECONDS))


# ── Provider-specific taxonomies ──────────────────────────────────────


def provider_taxonomy_openai_compat() -> ExceptionTaxonomy:
    """Exception classes raised by :mod:`llm_code.api.openai_compat`."""
    from llm_code.api.errors import (
        ProviderAuthError,
        ProviderConnectionError,
        ProviderModelNotFoundError,
        ProviderOverloadError,
        ProviderRateLimitError,
        ProviderTimeoutError,
    )
    import httpx

    return ExceptionTaxonomy(
        rate_limit_types=(ProviderRateLimitError,),
        overload_types=(ProviderOverloadError,),
        connection_types=(ProviderConnectionError, httpx.ConnectError),
        timeout_types=(
            ProviderTimeoutError,
            httpx.ReadTimeout,
            httpx.ConnectTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
        ),
        permanent_types=(ProviderAuthError, ProviderModelNotFoundError),
    )


def provider_taxonomy_anthropic() -> ExceptionTaxonomy:
    """Exception classes raised by :mod:`llm_code.api.anthropic_provider`."""
    from llm_code.api.errors import (
        ProviderAuthError,
        ProviderConnectionError,
        ProviderModelNotFoundError,
        ProviderOverloadError,
        ProviderRateLimitError,
        ProviderTimeoutError,
    )
    import httpx

    return ExceptionTaxonomy(
        rate_limit_types=(ProviderRateLimitError,),
        overload_types=(ProviderOverloadError,),
        connection_types=(ProviderConnectionError, httpx.ConnectError),
        timeout_types=(
            ProviderTimeoutError,
            httpx.ReadTimeout,
            httpx.ConnectTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
        ),
        permanent_types=(ProviderAuthError, ProviderModelNotFoundError),
    )


# ── Async wrapper ─────────────────────────────────────────────────────

T = TypeVar("T")


async def run_with_rate_limit(
    call: Callable[[], Awaitable[T]],
    handler: RateLimitHandler,
    taxonomy: ExceptionTaxonomy | None = None,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Loop ``call`` under ``handler``'s retry policy.

    Each invocation runs ``call()``. On success the handler is reset
    and the value returned. On exception the handler classifies it and
    decides whether to retry; if so we await ``sleep(decision.sleep_seconds)``
    and loop, otherwise the exception is re-raised.

    ``sleep`` is parametrised so tests can inject a synchronous stub
    without importing ``asyncio.sleep``.

    Typical use (inside a provider)::

        handler = RateLimitHandler(request_kind=RequestKind.FOREGROUND)
        resp = await run_with_rate_limit(
            lambda: self._client.post(url, json=payload),
            handler,
            taxonomy=provider_taxonomy_openai_compat(),
        )
    """
    tax = taxonomy if taxonomy is not None else handler.taxonomy
    if tax is None:
        raise ValueError(
            "run_with_rate_limit requires a taxonomy — pass one explicitly "
            "or set handler.taxonomy."
        )
    while True:
        try:
            value = await call()
        except BaseException as exc:
            decision = handler.on_exception(
                exc,
                rate_limit_types=tax.rate_limit_types,
                overload_types=tax.overload_types,
                connection_types=tax.connection_types,
                timeout_types=tax.timeout_types,
                permanent_types=tax.permanent_types,
            )
            if not decision.retry:
                raise
            await sleep(decision.sleep_seconds)
            continue
        handler.record_success()
        return value
