"""Tests for :mod:`llm_code.engine.concurrency` (M5 — Task 5.1)."""
from __future__ import annotations

import asyncio
import time

import pytest

from llm_code.engine.concurrency import (
    DEFAULT_GROUP,
    MAX_GROUP_PARALLELISM,
    assert_no_blocking_io,
    run_group_parallel,
)


class TestRunGroupParallel:
    async def test_empty_iterable_returns_empty_list(self):
        assert await run_group_parallel([]) == []

    async def test_preserves_input_order_even_when_completions_differ(self):
        # First coro sleeps longest; its value must still land at index 0.
        async def slow(value, delay):
            await asyncio.sleep(delay)
            return value

        coros = [slow("a", 0.03), slow("b", 0.01), slow("c", 0.02)]
        result = await run_group_parallel(coros)
        assert result == ["a", "b", "c"]

    async def test_respects_max_concurrency(self):
        in_flight = 0
        peak = 0
        lock = asyncio.Lock()

        async def gated(i):
            nonlocal in_flight, peak
            async with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            async with lock:
                in_flight -= 1
            return i

        await run_group_parallel([gated(i) for i in range(8)], max_concurrency=2)
        assert peak <= 2

    async def test_single_coro(self):
        async def one():
            return 42

        assert await run_group_parallel([one()]) == [42]

    async def test_invalid_max_concurrency_raises(self):
        with pytest.raises(ValueError):
            await run_group_parallel([], max_concurrency=0)

    async def test_exception_propagates(self):
        async def boom():
            raise RuntimeError("explode")

        async def ok():
            return 1

        with pytest.raises(RuntimeError, match="explode"):
            await run_group_parallel([ok(), boom()])

    async def test_actually_runs_in_parallel(self):
        """Four 10 ms sleeps must take <30 ms when max_concurrency >=4."""
        async def sleep10():
            await asyncio.sleep(0.01)
            return 1

        t0 = time.monotonic()
        await run_group_parallel([sleep10() for _ in range(4)], max_concurrency=4)
        elapsed = time.monotonic() - t0
        # Serial would be ≥40ms; 4-wide parallel should be ≤30ms even under load.
        assert elapsed < 0.03


class TestAssertNoBlockingIo:
    async def test_decorator_allows_asyncio_sleep(self):
        @assert_no_blocking_io(0.05)
        async def fn():
            await asyncio.sleep(0.01)
            return "ok"

        assert await fn() == "ok"

    async def test_decorator_restores_slow_callback_duration(self):
        loop = asyncio.get_running_loop()
        original = loop.slow_callback_duration

        @assert_no_blocking_io(0.01)
        async def fn():
            return "done"

        await fn()
        assert loop.slow_callback_duration == original


class TestConstants:
    def test_default_group_is_default(self):
        assert DEFAULT_GROUP == "default"

    def test_max_parallelism_is_bounded(self):
        assert 1 <= MAX_GROUP_PARALLELISM <= 32
