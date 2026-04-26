"""Tests for v15 M2 — proactive sliding-window rate limiter.

Coverage:

* Constructor validation.
* Sequential calls within window — no wait.
* Window full — next call awaits oldest rolloff.
* Window rolloff — sleep past the window allows new calls without wait.
* Concurrency cap — at most N in-flight at any time.
* Concurrency cap + rate window — both gates active simultaneously.
* Telemetry — ``wait_count`` increments on awaited acquires.
* Re-entry — same limiter usable across multiple calls.
* Cancellation — semaphore released on ``CancelledError``.
* Provider integration — profile field instantiates the limiter
  on both Anthropic and OpenAI-compat providers.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from llm_code.api.anthropic_provider import AnthropicProvider
from llm_code.api.openai_compat import OpenAICompatProvider
from llm_code.api.rate_limiter import SlidingWindowLimiter


# ── Constructor validation ────────────────────────────────────────────


class TestConstructorValidation:
    def test_zero_max_requests_raises(self) -> None:
        with pytest.raises(ValueError, match="max_requests must be > 0"):
            SlidingWindowLimiter(max_requests=0, window_seconds=60)

    def test_negative_max_requests_raises(self) -> None:
        with pytest.raises(ValueError, match="max_requests must be > 0"):
            SlidingWindowLimiter(max_requests=-1, window_seconds=60)

    def test_zero_window_raises(self) -> None:
        with pytest.raises(ValueError, match="window_seconds must be > 0"):
            SlidingWindowLimiter(max_requests=10, window_seconds=0)

    def test_negative_window_raises(self) -> None:
        with pytest.raises(ValueError, match="window_seconds must be > 0"):
            SlidingWindowLimiter(max_requests=10, window_seconds=-1.0)


# ── Sequential within window ──────────────────────────────────────────


class TestWithinWindow:
    @pytest.mark.asyncio
    async def test_three_calls_within_limit_no_wait(self) -> None:
        limiter = SlidingWindowLimiter(
            max_requests=10, window_seconds=60.0,
        )
        for _ in range(3):
            async with limiter:
                pass
        assert limiter.wait_count == 0


# ── Window full → wait ────────────────────────────────────────────────


class TestWindowFull:
    @pytest.mark.asyncio
    async def test_eleventh_call_waits_for_oldest_rolloff(self) -> None:
        # max=10 in 2s window; fire 10 immediately, 11th must wait.
        # Use a real but tiny window so the test runs fast.
        limiter = SlidingWindowLimiter(
            max_requests=10, window_seconds=0.5,
        )
        start = time.monotonic()
        for _ in range(10):
            async with limiter:
                pass
        # 11th call: window is full. Should wait until oldest rolls off.
        async with limiter:
            pass
        elapsed = time.monotonic() - start
        # 11th call had to wait roughly window length.
        assert elapsed >= 0.4
        assert limiter.wait_count >= 1


# ── Window rolloff ────────────────────────────────────────────────────


class TestWindowRolloff:
    @pytest.mark.asyncio
    async def test_after_window_passes_no_wait(self) -> None:
        limiter = SlidingWindowLimiter(
            max_requests=3, window_seconds=0.3,
        )
        for _ in range(3):
            async with limiter:
                pass
        # Sleep past the window → all timestamps roll off.
        await asyncio.sleep(0.4)
        wait_before = limiter.wait_count
        async with limiter:
            pass
        # No wait this time.
        assert limiter.wait_count == wait_before


# ── Concurrency cap ───────────────────────────────────────────────────


class TestConcurrencyCap:
    @pytest.mark.asyncio
    async def test_at_most_n_in_flight(self) -> None:
        limiter = SlidingWindowLimiter(
            max_requests=100, window_seconds=60.0,
            concurrency=3,
        )
        in_flight = 0
        max_in_flight = 0

        async def task() -> None:
            nonlocal in_flight, max_in_flight
            async with limiter:
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
                await asyncio.sleep(0.05)
                in_flight -= 1

        await asyncio.gather(*(task() for _ in range(10)))
        assert max_in_flight == 3

    @pytest.mark.asyncio
    async def test_no_concurrency_cap_when_zero(self) -> None:
        limiter = SlidingWindowLimiter(
            max_requests=100, window_seconds=60.0,
            concurrency=None,
        )
        # Can hold many in flight simultaneously.
        in_flight = 0
        max_in_flight = 0

        async def task() -> None:
            nonlocal in_flight, max_in_flight
            async with limiter:
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
                await asyncio.sleep(0.02)
                in_flight -= 1

        await asyncio.gather(*(task() for _ in range(8)))
        # No cap → all 8 ran together.
        assert max_in_flight >= 4  # at least multiple in flight


# ── Concurrency + rate combined ───────────────────────────────────────


class TestCombinedGates:
    @pytest.mark.asyncio
    async def test_concurrency_and_rate_both_active(self) -> None:
        # max_requests=5, window=0.5s, concurrency=2.
        # 5 tasks: each holds for 0.1s. Concurrency cap forces serial
        # batches of 2; rate cap won't be hit because we only have 5
        # acquires. Test asserts both gates are stable when active.
        limiter = SlidingWindowLimiter(
            max_requests=5, window_seconds=0.5,
            concurrency=2,
        )
        in_flight = 0
        max_in_flight = 0

        async def task() -> None:
            nonlocal in_flight, max_in_flight
            async with limiter:
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
                await asyncio.sleep(0.05)
                in_flight -= 1

        await asyncio.gather(*(task() for _ in range(5)))
        assert max_in_flight <= 2


# ── Telemetry ─────────────────────────────────────────────────────────


class TestTelemetry:
    @pytest.mark.asyncio
    async def test_wait_count_increments_on_wait(self) -> None:
        limiter = SlidingWindowLimiter(
            max_requests=2, window_seconds=0.3,
        )
        async with limiter:
            pass
        async with limiter:
            pass
        assert limiter.wait_count == 0
        # 3rd call must wait — increments wait_count.
        async with limiter:
            pass
        assert limiter.wait_count >= 1


# ── Re-entry ──────────────────────────────────────────────────────────


class TestReEntry:
    @pytest.mark.asyncio
    async def test_same_limiter_used_twice(self) -> None:
        limiter = SlidingWindowLimiter(
            max_requests=10, window_seconds=60.0,
        )
        # Two sequential acquisitions.
        async with limiter:
            pass
        async with limiter:
            pass
        # No exceptions = pass.


# ── Cancellation ──────────────────────────────────────────────────────


class TestCancellation:
    @pytest.mark.asyncio
    async def test_cancellation_releases_semaphore(self) -> None:
        # Concurrency=1; first task holds it forever. Second task
        # waits, then cancels. Semaphore must be free for a third task.
        limiter = SlidingWindowLimiter(
            max_requests=100, window_seconds=60.0,
            concurrency=1,
        )

        first_done = asyncio.Event()

        async def first() -> None:
            async with limiter:
                # Hold until cancelled.
                await asyncio.sleep(0.5)
                first_done.set()

        async def second() -> None:
            # Will await on the semaphore; we cancel before it acquires.
            async with limiter:
                pass

        first_task = asyncio.create_task(first())
        # Give first task a moment to acquire.
        await asyncio.sleep(0.05)
        second_task = asyncio.create_task(second())
        await asyncio.sleep(0.05)
        second_task.cancel()
        try:
            await second_task
        except asyncio.CancelledError:
            pass
        # First task still holding; semaphore not yet free until first
        # exits. Wait for first to finish.
        await first_task
        # After first exits, capacity should be 1 again.
        assert limiter.in_flight_capacity == 1


# ── Provider integration ──────────────────────────────────────────────


class TestProviderIntegration:
    """Profile field flips the limiter on/off on both providers."""

    @pytest.mark.asyncio
    async def test_openai_compat_no_limiter_when_disabled(self) -> None:
        provider = OpenAICompatProvider(
            base_url="http://example.com",
            api_key="x",
            model_name="default",
        )
        # Default profile — proactive_rate_limit_per_minute = 0.
        assert provider._limiter is None
        await provider.close()

    @pytest.mark.asyncio
    async def test_openai_compat_limiter_instantiated_when_enabled(
        self,
    ) -> None:
        # Inject into the live registry (not just _BUILTIN_PROFILES,
        # which the registry copies once on first init).
        from llm_code.runtime.model_profile import ModelProfile, get_registry

        registry = get_registry()
        marker = "v15-m2-test-profile"
        original = registry._profiles.get(marker)
        registry._profiles[marker] = ModelProfile(
            name=marker,
            proactive_rate_limit_per_minute=20,
            proactive_rate_limit_concurrency=3,
        )
        try:
            provider = OpenAICompatProvider(
                base_url="http://example.com",
                api_key="x",
                model_name=marker,
            )
            assert provider._limiter is not None
            assert provider._limiter._max == 20
            await provider.close()
        finally:
            if original is None:
                del registry._profiles[marker]
            else:
                registry._profiles[marker] = original

    @pytest.mark.asyncio
    async def test_anthropic_no_limiter_when_disabled(self) -> None:
        provider = AnthropicProvider(
            api_key="x",
            model_name="claude-sonnet-4-6",
        )
        assert provider._limiter is None
        await provider.close()

    @pytest.mark.asyncio
    async def test_anthropic_limiter_instantiated_when_enabled(
        self,
    ) -> None:
        from llm_code.runtime.model_profile import ModelProfile, get_registry

        registry = get_registry()
        marker = "v15-m2-test-anthropic"
        original = registry._profiles.get(marker)
        registry._profiles[marker] = ModelProfile(
            name=marker,
            provider_type="anthropic",
            proactive_rate_limit_per_minute=15,
        )
        try:
            provider = AnthropicProvider(
                api_key="x",
                model_name=marker,
            )
            assert provider._limiter is not None
            assert provider._limiter._max == 15
            await provider.close()
        finally:
            if original is None:
                del registry._profiles[marker]
            else:
                registry._profiles[marker] = original


# ── End-to-end: limiter gates real HTTP path ──────────────────────────


class TestEndToEnd:
    @pytest.mark.asyncio
    async def test_proactive_wait_delays_eleventh_post(self) -> None:
        """Eleventh POST start time must be ≥ window length after the
        first.

        Uses small window for test speed (0.4s, 10/window), then fires
        11 POSTs and verifies the 11th was delayed.
        """
        from llm_code.runtime.model_profile import ModelProfile, get_registry

        registry = get_registry()
        marker = "v15-m2-e2e-profile"
        original = registry._profiles.get(marker)
        registry._profiles[marker] = ModelProfile(
            name=marker,
            proactive_rate_limit_per_minute=10,
        )
        try:
            provider = OpenAICompatProvider(
                base_url="http://example.com",
                api_key="x",
                model_name=marker,
            )
            # Override the window for fast testing.
            assert provider._limiter is not None
            provider._limiter._window = 0.4

            mock_response = httpx.Response(200, json={"ok": True})
            post_times: list[float] = []

            async def fake_post(*args, **kwargs):
                post_times.append(time.monotonic())
                return mock_response

            with patch.object(
                provider._client, "post",
                new=AsyncMock(side_effect=fake_post),
            ):
                # Fire 11 POSTs serially via the limiter.
                for _ in range(11):
                    await provider._post_with_proactive_limit(
                        "http://example.com/x", {},
                    )
            # 11th POST must be at least window length after the first.
            elapsed = post_times[-1] - post_times[0]
            assert elapsed >= 0.35, (
                f"11th call started only {elapsed:.3f}s after 1st; "
                f"expected ≥ 0.35s window-length wait"
            )
            await provider.close()
        finally:
            if original is None:
                del registry._profiles[marker]
            else:
                registry._profiles[marker] = original
