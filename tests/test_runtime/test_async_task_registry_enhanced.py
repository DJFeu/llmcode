"""Tests for AsyncTaskRegistry concurrency cap and stability polling."""
from __future__ import annotations

import asyncio

import pytest

from llm_code.runtime.background_task_registry import AsyncTaskRegistry


@pytest.mark.asyncio
async def test_max_concurrent_cap_refuses_overflow() -> None:
    reg = AsyncTaskRegistry(max_concurrent=2)

    async def worker():
        await asyncio.sleep(0.5)

    t1 = asyncio.create_task(worker())
    t2 = asyncio.create_task(worker())
    t3 = asyncio.create_task(worker())

    id1 = reg.register(t1, "a")
    id2 = reg.register(t2, "b")
    id3 = reg.register(t3, "c")

    assert id1 is not None
    assert id2 is not None
    assert id3 is None  # over cap

    for t in (t1, t2, t3):
        t.cancel()
    await asyncio.gather(t1, t2, t3, return_exceptions=True)


@pytest.mark.asyncio
async def test_max_concurrent_property() -> None:
    reg = AsyncTaskRegistry(max_concurrent=7)
    assert reg.max_concurrent == 7


@pytest.mark.asyncio
async def test_poll_until_stable_done() -> None:
    reg = AsyncTaskRegistry()

    async def quick():
        return 42

    task = asyncio.create_task(quick())
    tid = reg.register(task, "quick")
    assert tid is not None
    await task
    state = await reg.poll_until_stable(tid, interval=0.01, stable_count=2)
    # After completion, callback unregisters -> "missing"
    assert state in ("missing", "done")


@pytest.mark.asyncio
async def test_poll_until_stable_missing_for_unknown_id() -> None:
    reg = AsyncTaskRegistry()
    state = await reg.poll_until_stable("nonexistent", interval=0.01, stable_count=2)
    assert state == "missing"
