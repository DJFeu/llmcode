"""Concurrency helpers for AsyncPipeline (M5 — Task 5.1).

A concurrency group is a set of components the AsyncPipeline may execute
in parallel once their upstream dependencies are satisfied. Components
declare their group via a class attribute::

    @component
    class MyFastComponent:
        concurrency_group = "fast_io"
        ...

This module also exposes :func:`assert_no_blocking_io`, a test-only
decorator that tightens ``loop.slow_callback_duration`` around the
wrapped coroutine — accidental blocking I/O inside ``async def`` is the
biggest regression risk introduced by the engine-wide asyncio conversion,
so tests can opt into stricter detection.

Spec: docs/superpowers/specs/2026-04-21-llm-code-v12-haystack-borrow-design.md §5.5
Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-async-pipeline.md Task 5.1
"""
from __future__ import annotations

import asyncio
import functools
import warnings
from typing import Awaitable, Iterable, List, TypeVar


DEFAULT_GROUP: str = "default"
# Cap total in-flight coros per group to avoid fd / socket exhaustion.
# The number 8 is load-bearing — matches default asyncio.to_thread pool
# sizing and keeps memory ≤110 % of sync baseline (see plan §5.10).
MAX_GROUP_PARALLELISM: int = 8


T = TypeVar("T")


async def run_group_parallel(
    coros: Iterable[Awaitable[T]],
    *,
    max_concurrency: int = MAX_GROUP_PARALLELISM,
) -> List[T]:
    """Run ``coros`` with bounded concurrency; preserve input order in results.

    Args:
        coros: Iterable of already-constructed coroutine objects. Each
            is awaited exactly once. Non-awaitable items raise ``TypeError``
            when the scheduler tries to await them.
        max_concurrency: Upper bound on simultaneous in-flight coros.
            Must be ≥ 1. Defaults to :data:`MAX_GROUP_PARALLELISM`.

    Returns:
        List of awaited results in the **input** order — not completion
        order. Callers that zip these with a parallel list of component
        names (``AsyncPipeline``) depend on this guarantee.

    Raises:
        ValueError: if ``max_concurrency < 1``.
        Any exception raised by an awaited coroutine propagates.
        Pending tasks are cancelled via :func:`asyncio.gather` return_exceptions=False.
    """
    if max_concurrency < 1:
        raise ValueError(f"max_concurrency must be >= 1, got {max_concurrency}")

    # Materialise eagerly so we can tag each coroutine with its position
    # before scheduling (otherwise exhaustion order would determine order).
    indexed = list(enumerate(coros))
    if not indexed:
        return []

    sem = asyncio.Semaphore(max_concurrency)

    async def _bounded(idx: int, coro: Awaitable[T]) -> tuple[int, T]:
        async with sem:
            value = await coro
            return idx, value

    tasks = [asyncio.create_task(_bounded(i, c)) for i, c in indexed]
    # ``return_exceptions=False`` so the first failure propagates; other
    # tasks will be awaited to drain them (asyncio.gather cancels siblings
    # automatically when one errors).
    results = await asyncio.gather(*tasks)
    # Sort by input index so ordering matches callers' expectations.
    results_sorted = sorted(results, key=lambda x: x[0])
    return [r[1] for r in results_sorted]


def assert_no_blocking_io(threshold_s: float = 0.1):
    """Test decorator: flag coroutines that exceed ``threshold_s`` in a single tick.

    Usage in tests::

        @assert_no_blocking_io(0.05)
        async def test_my_component_does_not_block():
            ...

    Mechanism: temporarily tightens ``loop.slow_callback_duration`` so
    asyncio's built-in slow-callback warning fires earlier, and promotes
    :class:`ResourceWarning` to an error for the duration of the call.

    The decorator is inert at module-import time — it only activates
    when the coroutine actually runs, so wrapping a helper that isn't
    executed adds no cost.
    """
    def _wrap(afn):
        @functools.wraps(afn)
        async def _inner(*args, **kwargs):
            loop = asyncio.get_running_loop()
            original = loop.slow_callback_duration
            loop.slow_callback_duration = threshold_s
            with warnings.catch_warnings():
                warnings.simplefilter("error", ResourceWarning)
                try:
                    return await afn(*args, **kwargs)
                finally:
                    loop.slow_callback_duration = original
        return _inner
    return _wrap


__all__ = [
    "DEFAULT_GROUP",
    "MAX_GROUP_PARALLELISM",
    "run_group_parallel",
    "assert_no_blocking_io",
]
