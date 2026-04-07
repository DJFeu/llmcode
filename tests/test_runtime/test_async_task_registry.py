"""Tests for AsyncTaskRegistry and the global accessor."""
from __future__ import annotations

import asyncio

import pytest

from llm_code.runtime.background_task_registry import (
    AsyncTaskRegistry,
    global_async_registry,
)


@pytest.mark.asyncio
async def test_register_increments_active_count() -> None:
    reg = AsyncTaskRegistry()
    assert reg.active_count() == 0

    started = asyncio.Event()
    release = asyncio.Event()

    async def _worker() -> str:
        started.set()
        await release.wait()
        return "done"

    task = asyncio.create_task(_worker())
    reg.register(task, "worker")
    await started.wait()

    assert reg.active_count() == 1
    assert len(reg.list_active()) == 1
    assert reg.list_active()[0].title == "worker"

    release.set()
    await task
    # Done callback fires asynchronously — give the loop one tick.
    await asyncio.sleep(0)
    assert reg.active_count() == 0


@pytest.mark.asyncio
async def test_unregister_returns_info() -> None:
    reg = AsyncTaskRegistry()

    async def _noop() -> None:
        await asyncio.sleep(10)

    task = asyncio.create_task(_noop())
    tid = reg.register(task, "noop")
    info = reg.unregister(tid)
    assert info is not None
    assert info.title == "noop"
    assert reg.unregister(tid) is None
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_cancel_all_within_budget() -> None:
    reg = AsyncTaskRegistry()

    async def _slow() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            await asyncio.sleep(0)
            raise

    tasks = [asyncio.create_task(_slow()) for _ in range(3)]
    for t in tasks:
        reg.register(t, "slow")

    assert reg.active_count() == 3

    snapshot = await reg.cancel_all(timeout=2.0)
    assert len(snapshot) == 3
    for t in tasks:
        assert t.cancelled() or t.done()
    await asyncio.sleep(0)
    assert reg.active_count() == 0


@pytest.mark.asyncio
async def test_cancel_all_empty_returns_empty() -> None:
    reg = AsyncTaskRegistry()
    assert await reg.cancel_all(timeout=1.0) == []


def test_global_async_registry_is_singleton() -> None:
    a = global_async_registry()
    b = global_async_registry()
    assert a is b
