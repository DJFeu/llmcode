"""Tests for :mod:`llm_code.engine.policies.retry`."""
from __future__ import annotations

import pytest

from llm_code.engine.policies import RetryDecision, RetryPolicy
from llm_code.engine.policies.retry import (
    CompositeRetryPolicy,
    ExponentialBackoff,
    NoRetry,
    RetryOnRateLimit,
)


# ---------------------------------------------------------------------------
# NoRetry
# ---------------------------------------------------------------------------


class TestNoRetry:
    def test_never_retries(self):
        policy = NoRetry()
        decision = policy.should_retry(RuntimeError("boom"), 0, {})
        assert decision.should_retry is False
        assert decision.delay_ms == 0
        assert "no-retry" in decision.reason

    def test_protocol_conformance(self):
        assert isinstance(NoRetry(), RetryPolicy)

    def test_decision_is_frozen(self):
        decision = NoRetry().should_retry(ValueError(), 0, {})
        with pytest.raises(Exception):
            decision.should_retry = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ExponentialBackoff
# ---------------------------------------------------------------------------


class TestExponentialBackoff:
    def test_validates_max_attempts(self):
        with pytest.raises(ValueError):
            ExponentialBackoff(max_attempts=0)

    def test_validates_ms_ranges(self):
        with pytest.raises(ValueError):
            ExponentialBackoff(base_ms=-1)
        with pytest.raises(ValueError):
            ExponentialBackoff(cap_ms=-5)

    def test_stops_at_max_attempts(self):
        policy = ExponentialBackoff(max_attempts=3)
        decision = policy.should_retry(ConnectionResetError("x"), 3, {})
        assert decision.should_retry is False
        assert "max attempts" in decision.reason

    def test_non_transient_no_retry(self):
        policy = ExponentialBackoff()
        decision = policy.should_retry(ValueError("bad arg"), 0, {})
        assert decision.should_retry is False
        assert "non-transient" in decision.reason

    def test_transient_retries_with_backoff(self):
        policy = ExponentialBackoff(base_ms=100, cap_ms=10000)
        # attempt 0 -> 100 * 2^0 = 100 ms
        d0 = policy.should_retry(ConnectionResetError(), 0, {})
        assert d0.should_retry is True
        assert d0.delay_ms == 100
        # attempt 2 -> 100 * 4 = 400 ms
        d2 = policy.should_retry(ConnectionResetError(), 2, {})
        assert d2.delay_ms == 400

    def test_respects_cap(self):
        policy = ExponentialBackoff(max_attempts=10, base_ms=1000, cap_ms=3000)
        # attempt 10 would be 1000 * 2^10, way over cap
        decision = policy.should_retry(ConnectionResetError(), 5, {})
        assert decision.delay_ms == 3000  # capped

    def test_timeout_error_treated_as_transient(self):
        policy = ExponentialBackoff()
        decision = policy.should_retry(TimeoutError("slow"), 0, {})
        assert decision.should_retry is True

    def test_httpx_errors_are_transient_if_installed(self):
        httpx = pytest.importorskip("httpx")
        policy = ExponentialBackoff()
        err = httpx.ConnectError("refused")
        decision = policy.should_retry(err, 0, {})
        assert decision.should_retry is True


# ---------------------------------------------------------------------------
# RetryOnRateLimit
# ---------------------------------------------------------------------------


class _RateLimitByName(Exception):
    """Class whose name matches the rate-limit duck-type check."""

    pass


class TestRetryOnRateLimit:
    def test_validates_max_attempts(self):
        with pytest.raises(ValueError):
            RetryOnRateLimit(max_attempts=0)

    def test_detects_ratelimit_by_class_name(self):
        policy = RetryOnRateLimit(default_delay_ms=500)
        decision = policy.should_retry(_RateLimitByName(), 0, {})
        assert decision.should_retry is True
        assert decision.delay_ms == 500

    def test_non_rate_limit_does_not_retry(self):
        policy = RetryOnRateLimit()
        decision = policy.should_retry(ValueError("nope"), 0, {})
        assert decision.should_retry is False
        assert "not a rate-limit" in decision.reason

    def test_max_attempts_exhausts(self):
        policy = RetryOnRateLimit(max_attempts=2)
        decision = policy.should_retry(_RateLimitByName(), 2, {})
        assert decision.should_retry is False

    def test_retry_after_seconds_attribute(self):
        err = _RateLimitByName()
        err.retry_after_seconds = 3
        policy = RetryOnRateLimit()
        decision = policy.should_retry(err, 0, {})
        assert decision.delay_ms == 3000

    def test_retry_after_attribute(self):
        err = _RateLimitByName()
        err.retry_after = 2.5
        policy = RetryOnRateLimit()
        decision = policy.should_retry(err, 0, {})
        assert decision.delay_ms == 2500

    def test_status_code_429(self):
        err = Exception("throttled")
        err.status_code = 429
        policy = RetryOnRateLimit()
        decision = policy.should_retry(err, 0, {})
        assert decision.should_retry is True

    def test_retry_after_header(self):
        class _ErrWithHeaders(Exception):
            pass
        err = _ErrWithHeaders()
        err.status_code = 429
        err.headers = {"Retry-After": "7"}
        policy = RetryOnRateLimit()
        decision = policy.should_retry(err, 0, {})
        assert decision.delay_ms == 7000

    def test_malformed_retry_after_falls_back_to_default(self):
        err = _RateLimitByName()
        err.retry_after = "not-a-number"
        policy = RetryOnRateLimit(default_delay_ms=999)
        decision = policy.should_retry(err, 0, {})
        assert decision.delay_ms == 999


# ---------------------------------------------------------------------------
# CompositeRetryPolicy
# ---------------------------------------------------------------------------


class _AlwaysRetry:
    def __init__(self, tag: str):
        self.tag = tag

    def should_retry(self, error, attempt, state):
        return RetryDecision(should_retry=True, delay_ms=0, reason=self.tag)


class _NeverRetry:
    def __init__(self, tag: str):
        self.tag = tag

    def should_retry(self, error, attempt, state):
        return RetryDecision(should_retry=False, reason=self.tag)


class TestCompositeRetryPolicy:
    def test_requires_at_least_one_child(self):
        with pytest.raises(ValueError):
            CompositeRetryPolicy([])

    def test_first_matching_wins(self):
        a = _AlwaysRetry("A")
        b = _AlwaysRetry("B")
        composite = CompositeRetryPolicy([a, b])
        decision = composite.should_retry(Exception(), 0, {})
        assert decision.reason == "A"

    def test_falls_through_to_later_child(self):
        composite = CompositeRetryPolicy([_NeverRetry("no"), _AlwaysRetry("yes")])
        decision = composite.should_retry(Exception(), 0, {})
        assert decision.should_retry is True
        assert decision.reason == "yes"

    def test_all_reject_surfaces_last_reason(self):
        composite = CompositeRetryPolicy([_NeverRetry("first"), _NeverRetry("last")])
        decision = composite.should_retry(Exception(), 0, {})
        assert decision.should_retry is False
        assert decision.reason == "last"

    def test_rate_limit_then_exponential(self):
        composite = CompositeRetryPolicy(
            [RetryOnRateLimit(max_attempts=5), ExponentialBackoff(max_attempts=3)]
        )
        # Rate-limit path
        rl = _RateLimitByName()
        d1 = composite.should_retry(rl, 0, {})
        assert d1.should_retry is True
        assert "rate-limit" in d1.reason
        # Transient path via exponential
        d2 = composite.should_retry(ConnectionResetError(), 0, {})
        assert d2.should_retry is True
        assert "transient" in d2.reason
