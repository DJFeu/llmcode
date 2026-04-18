"""Tests for the rate-limiter policy module (C3a).

The existing provider code (openai_compat.py, anthropic_provider.py)
already has its own retry loops. The rate_limiter module factors out
the *policy* decisions so both providers — and any future provider —
share one implementation of:

    * classify(exc)                   → which retry track applies
    * next_backoff(classification, …) → how long to sleep
    * should_retry(...)               → continue or bail
    * heartbeat dispatch              → keep-alive output every N seconds

Provider loops keep calling httpx themselves; they just consult these
pure functions for the decisions. This keeps the change surface small
and backward-compatible.
"""
from __future__ import annotations

import pytest

from llm_code.api.rate_limiter import (
    RateLimitClassification,
    RateLimitHandler,
    RequestKind,
    classify_exception,
    next_backoff,
    should_retry,
)


# ---------- classify_exception ----------


class FakeRateLimitError(Exception):
    def __init__(self, msg: str = "", retry_after: float | None = None) -> None:
        super().__init__(msg)
        self.retry_after = retry_after


class FakeOverloadError(Exception):
    pass


class FakeConnectionError(Exception):
    pass


class FakeTimeoutError(Exception):
    pass


class FakeAuthError(Exception):
    pass


class TestClassifyException:
    def test_rate_limit_maps_to_429(self) -> None:
        c = classify_exception(
            FakeRateLimitError("limit"),
            rate_limit_types=(FakeRateLimitError,),
            overload_types=(FakeOverloadError,),
            connection_types=(FakeConnectionError,),
            timeout_types=(FakeTimeoutError,),
            permanent_types=(FakeAuthError,),
        )
        assert c is RateLimitClassification.RATE_LIMIT

    def test_overload_maps_to_529(self) -> None:
        c = classify_exception(
            FakeOverloadError(),
            rate_limit_types=(FakeRateLimitError,),
            overload_types=(FakeOverloadError,),
            connection_types=(FakeConnectionError,),
            timeout_types=(FakeTimeoutError,),
            permanent_types=(FakeAuthError,),
        )
        assert c is RateLimitClassification.OVERLOAD

    def test_connection_error(self) -> None:
        c = classify_exception(
            FakeConnectionError(),
            rate_limit_types=(FakeRateLimitError,),
            overload_types=(FakeOverloadError,),
            connection_types=(FakeConnectionError,),
            timeout_types=(FakeTimeoutError,),
            permanent_types=(FakeAuthError,),
        )
        assert c is RateLimitClassification.CONNECTION

    def test_timeout(self) -> None:
        c = classify_exception(
            FakeTimeoutError(),
            rate_limit_types=(FakeRateLimitError,),
            overload_types=(FakeOverloadError,),
            connection_types=(FakeConnectionError,),
            timeout_types=(FakeTimeoutError,),
            permanent_types=(FakeAuthError,),
        )
        assert c is RateLimitClassification.TIMEOUT

    def test_permanent_error(self) -> None:
        c = classify_exception(
            FakeAuthError(),
            rate_limit_types=(FakeRateLimitError,),
            overload_types=(FakeOverloadError,),
            connection_types=(FakeConnectionError,),
            timeout_types=(FakeTimeoutError,),
            permanent_types=(FakeAuthError,),
        )
        assert c is RateLimitClassification.PERMANENT

    def test_unknown_falls_through_to_unknown(self) -> None:
        c = classify_exception(
            RuntimeError(),
            rate_limit_types=(FakeRateLimitError,),
            overload_types=(FakeOverloadError,),
            connection_types=(FakeConnectionError,),
            timeout_types=(FakeTimeoutError,),
            permanent_types=(FakeAuthError,),
        )
        assert c is RateLimitClassification.UNKNOWN


# ---------- next_backoff ----------


class TestNextBackoff:
    def test_rate_limit_uses_retry_after_when_present(self) -> None:
        b = next_backoff(
            RateLimitClassification.RATE_LIMIT,
            attempt=0,
            overload_attempt=0,
            retry_after=12.5,
        )
        assert b == 12.5

    def test_rate_limit_exponential_when_no_hint(self) -> None:
        # 500ms base * 2 ** attempt, clamped at max
        assert next_backoff(RateLimitClassification.RATE_LIMIT, attempt=0, overload_attempt=0) == 0.5
        assert next_backoff(RateLimitClassification.RATE_LIMIT, attempt=1, overload_attempt=0) == 1.0
        assert next_backoff(RateLimitClassification.RATE_LIMIT, attempt=2, overload_attempt=0) == 2.0
        assert next_backoff(RateLimitClassification.RATE_LIMIT, attempt=10, overload_attempt=0) <= 300.0

    def test_overload_uses_long_track(self) -> None:
        assert next_backoff(RateLimitClassification.OVERLOAD, attempt=0, overload_attempt=0) == 30.0
        assert next_backoff(RateLimitClassification.OVERLOAD, attempt=0, overload_attempt=1) == 60.0
        assert next_backoff(RateLimitClassification.OVERLOAD, attempt=0, overload_attempt=2) == 120.0
        # Beyond defined schedule → caps at persistent max (5 min)
        assert next_backoff(RateLimitClassification.OVERLOAD, attempt=0, overload_attempt=10) == 300.0

    def test_connection_and_timeout_exponential(self) -> None:
        for kind in (RateLimitClassification.CONNECTION, RateLimitClassification.TIMEOUT):
            assert next_backoff(kind, attempt=0, overload_attempt=0) == 1.0
            assert next_backoff(kind, attempt=2, overload_attempt=0) == 4.0


# ---------- should_retry ----------


class TestShouldRetry:
    def test_foreground_allows_more_attempts(self) -> None:
        # FG default: MAX_RETRIES=10
        decision = should_retry(
            RateLimitClassification.RATE_LIMIT,
            attempt=5,
            overload_attempt=0,
            request_kind=RequestKind.FOREGROUND,
        )
        assert decision.retry is True
        assert decision.sleep_seconds > 0

    def test_background_bails_on_rate_limit(self) -> None:
        decision = should_retry(
            RateLimitClassification.RATE_LIMIT,
            attempt=0,
            overload_attempt=0,
            request_kind=RequestKind.BACKGROUND,
        )
        assert decision.retry is False

    def test_foreground_overload_persistent_until_limit(self) -> None:
        # Overload on FG is persistent — can loop far past normal budget
        decision = should_retry(
            RateLimitClassification.OVERLOAD,
            attempt=5,
            overload_attempt=3,
            request_kind=RequestKind.FOREGROUND,
        )
        assert decision.retry is True

    def test_permanent_never_retries(self) -> None:
        decision = should_retry(
            RateLimitClassification.PERMANENT,
            attempt=0,
            overload_attempt=0,
            request_kind=RequestKind.FOREGROUND,
        )
        assert decision.retry is False

    def test_unknown_retried_once_on_foreground(self) -> None:
        decision = should_retry(
            RateLimitClassification.UNKNOWN,
            attempt=0,
            overload_attempt=0,
            request_kind=RequestKind.FOREGROUND,
        )
        assert decision.retry is True
        # But a second attempt already bails
        decision2 = should_retry(
            RateLimitClassification.UNKNOWN,
            attempt=1,
            overload_attempt=0,
            request_kind=RequestKind.FOREGROUND,
        )
        assert decision2.retry is False


# ---------- RateLimitHandler (stateful orchestrator) ----------


class TestRateLimitHandler:
    def test_records_and_resets_state(self) -> None:
        h = RateLimitHandler(request_kind=RequestKind.FOREGROUND)
        d1 = h.on_exception(FakeRateLimitError(retry_after=1.5),
                            rate_limit_types=(FakeRateLimitError,),
                            overload_types=(FakeOverloadError,),
                            connection_types=(FakeConnectionError,),
                            timeout_types=(FakeTimeoutError,),
                            permanent_types=(FakeAuthError,))
        assert d1.retry is True
        assert d1.sleep_seconds == 1.5
        h.record_success()  # clears counters
        # After success the attempt counter must reset to 0
        d2 = h.on_exception(FakeRateLimitError(),
                            rate_limit_types=(FakeRateLimitError,),
                            overload_types=(FakeOverloadError,),
                            connection_types=(FakeConnectionError,),
                            timeout_types=(FakeTimeoutError,),
                            permanent_types=(FakeAuthError,))
        # Base exponential with attempt=0 → 0.5s
        assert d2.sleep_seconds == 0.5

    def test_overload_heartbeat_fires(self) -> None:
        beats: list[dict] = []

        def beat(info: dict) -> None:
            beats.append(info)

        h = RateLimitHandler(
            request_kind=RequestKind.FOREGROUND,
            heartbeat_interval=0.0,  # fire every beat for testing
            on_heartbeat=beat,
        )
        h.on_exception(FakeOverloadError(),
                       rate_limit_types=(FakeRateLimitError,),
                       overload_types=(FakeOverloadError,),
                       connection_types=(FakeConnectionError,),
                       timeout_types=(FakeTimeoutError,),
                       permanent_types=(FakeAuthError,))
        assert len(beats) == 1
        assert beats[0]["classification"] == RateLimitClassification.OVERLOAD.value
        assert beats[0]["overload_attempt"] == 1

    @pytest.mark.asyncio
    async def test_run_with_rate_limit_success_path(self) -> None:
        from llm_code.api.rate_limiter import ExceptionTaxonomy, run_with_rate_limit

        async def call():
            return 42

        handler = RateLimitHandler(request_kind=RequestKind.FOREGROUND)
        result = await run_with_rate_limit(
            call, handler, taxonomy=ExceptionTaxonomy(),
        )
        assert result == 42

    @pytest.mark.asyncio
    async def test_run_with_rate_limit_retries_then_succeeds(self) -> None:
        from llm_code.api.rate_limiter import ExceptionTaxonomy, run_with_rate_limit

        attempts = {"n": 0}

        async def call():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise FakeRateLimitError(retry_after=0.0)
            return "ok"

        async def no_sleep(_seconds: float) -> None:
            pass

        handler = RateLimitHandler(request_kind=RequestKind.FOREGROUND)
        tax = ExceptionTaxonomy(
            rate_limit_types=(FakeRateLimitError,),
            permanent_types=(FakeAuthError,),
        )
        result = await run_with_rate_limit(call, handler, tax, sleep=no_sleep)
        assert result == "ok"
        assert attempts["n"] == 3
        # Handler state reset on success
        assert handler.attempt == 0

    @pytest.mark.asyncio
    async def test_run_with_rate_limit_permanent_reraises(self) -> None:
        from llm_code.api.rate_limiter import ExceptionTaxonomy, run_with_rate_limit

        async def call():
            raise FakeAuthError("bad key")

        handler = RateLimitHandler(request_kind=RequestKind.FOREGROUND)
        tax = ExceptionTaxonomy(
            rate_limit_types=(FakeRateLimitError,),
            permanent_types=(FakeAuthError,),
        )

        async def no_sleep(_seconds: float) -> None:
            pass

        with pytest.raises(FakeAuthError):
            await run_with_rate_limit(call, handler, tax, sleep=no_sleep)

    @pytest.mark.asyncio
    async def test_run_with_rate_limit_bg_bails_on_rate_limit(self) -> None:
        from llm_code.api.rate_limiter import ExceptionTaxonomy, run_with_rate_limit

        async def call():
            raise FakeRateLimitError()

        handler = RateLimitHandler(request_kind=RequestKind.BACKGROUND)
        tax = ExceptionTaxonomy(rate_limit_types=(FakeRateLimitError,))

        async def no_sleep(_seconds: float) -> None:
            pass

        # Background requests bail immediately on rate-limit — first
        # decision is "don't retry" and the exception is re-raised.
        with pytest.raises(FakeRateLimitError):
            await run_with_rate_limit(call, handler, tax, sleep=no_sleep)

    def test_heartbeat_suppressed_under_interval(self) -> None:
        beats: list[dict] = []

        def beat(info: dict) -> None:
            beats.append(info)

        h = RateLimitHandler(
            request_kind=RequestKind.FOREGROUND,
            heartbeat_interval=30.0,  # long; should not fire back-to-back
            on_heartbeat=beat,
        )
        # Two rapid overloads within the interval → one heartbeat
        h.on_exception(FakeOverloadError(),
                       rate_limit_types=(FakeRateLimitError,),
                       overload_types=(FakeOverloadError,),
                       connection_types=(FakeConnectionError,),
                       timeout_types=(FakeTimeoutError,),
                       permanent_types=(FakeAuthError,))
        h.on_exception(FakeOverloadError(),
                       rate_limit_types=(FakeRateLimitError,),
                       overload_types=(FakeOverloadError,),
                       connection_types=(FakeConnectionError,),
                       timeout_types=(FakeTimeoutError,),
                       permanent_types=(FakeAuthError,))
        assert len(beats) == 1


# ---------- Provider taxonomies ----------


class TestProviderTaxonomy:
    def test_openai_compat_taxonomy_includes_provider_errors(self) -> None:
        from llm_code.api.errors import (
            ProviderAuthError,
            ProviderOverloadError,
            ProviderRateLimitError,
        )
        from llm_code.api.rate_limiter import provider_taxonomy_openai_compat

        tax = provider_taxonomy_openai_compat()
        assert ProviderRateLimitError in tax.rate_limit_types
        assert ProviderOverloadError in tax.overload_types
        assert ProviderAuthError in tax.permanent_types

    def test_openai_compat_taxonomy_includes_httpx_transport(self) -> None:
        import httpx

        from llm_code.api.rate_limiter import provider_taxonomy_openai_compat

        tax = provider_taxonomy_openai_compat()
        assert httpx.ReadTimeout in tax.timeout_types
        assert httpx.ConnectError in tax.connection_types

    def test_anthropic_taxonomy_matches_shape(self) -> None:
        from llm_code.api.errors import ProviderOverloadError
        from llm_code.api.rate_limiter import provider_taxonomy_anthropic

        tax = provider_taxonomy_anthropic()
        assert ProviderOverloadError in tax.overload_types
        assert len(tax.rate_limit_types) >= 1
