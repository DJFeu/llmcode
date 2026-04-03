"""Tests for cron scheduler."""
from __future__ import annotations

import asyncio
import datetime
from unittest.mock import AsyncMock

import pytest

from llm_code.cron.scheduler import CronScheduler
from llm_code.cron.storage import CronStorage, CronTask


@pytest.fixture
def storage(tmp_path):
    return CronStorage(tmp_path / ".llm-code" / "scheduled_tasks.json")


@pytest.fixture
def lock_path(tmp_path):
    return tmp_path / ".llm-code" / "cron.lock"


class TestCronScheduler:
    @pytest.mark.asyncio
    async def test_fires_due_task(self, storage, lock_path):
        callback = AsyncMock()
        storage.add(cron="* * * * *", prompt="hello", recurring=False, permanent=False)
        scheduler = CronScheduler(storage, lock_path, callback)

        # Run one tick
        await scheduler._tick(now=datetime.datetime.now() + datetime.timedelta(minutes=2))
        callback.assert_called_once_with("hello")

    @pytest.mark.asyncio
    async def test_does_not_fire_future_task(self, storage, lock_path):
        callback = AsyncMock()
        storage.add(cron="0 0 1 1 *", prompt="new year", recurring=True, permanent=True)
        scheduler = CronScheduler(storage, lock_path, callback)

        await scheduler._tick(now=datetime.datetime(2026, 6, 15, 12, 0))
        callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_recurring_removed_after_fire(self, storage, lock_path):
        callback = AsyncMock()
        storage.add(cron="* * * * *", prompt="once", recurring=False, permanent=False)
        scheduler = CronScheduler(storage, lock_path, callback)

        await scheduler._tick(now=datetime.datetime.now() + datetime.timedelta(minutes=2))
        assert len(storage.list_all()) == 0

    @pytest.mark.asyncio
    async def test_recurring_not_removed(self, storage, lock_path):
        callback = AsyncMock()
        storage.add(cron="* * * * *", prompt="repeat", recurring=True, permanent=False)
        scheduler = CronScheduler(storage, lock_path, callback)

        await scheduler._tick(now=datetime.datetime.now() + datetime.timedelta(minutes=2))
        assert len(storage.list_all()) == 1

    @pytest.mark.asyncio
    async def test_auto_expire_after_30_days(self, storage, lock_path):
        callback = AsyncMock()
        task = storage.add(cron="* * * * *", prompt="old", recurring=True, permanent=False)
        # Manually backdate created_at
        old_task = CronTask(
            id=task.id,
            cron=task.cron,
            prompt=task.prompt,
            recurring=True,
            permanent=False,
            created_at=datetime.datetime.now() - datetime.timedelta(days=31),
        )
        storage._tasks = [old_task]
        storage._save()

        scheduler = CronScheduler(storage, lock_path, callback)
        await scheduler._tick(now=datetime.datetime.now() + datetime.timedelta(minutes=2))
        callback.assert_not_called()
        assert len(storage.list_all()) == 0

    @pytest.mark.asyncio
    async def test_permanent_not_expired(self, storage, lock_path):
        callback = AsyncMock()
        task = storage.add(cron="* * * * *", prompt="forever", recurring=True, permanent=True)
        # Backdate
        old_task = CronTask(
            id=task.id,
            cron=task.cron,
            prompt=task.prompt,
            recurring=True,
            permanent=True,
            created_at=datetime.datetime.now() - datetime.timedelta(days=60),
        )
        storage._tasks = [old_task]
        storage._save()

        scheduler = CronScheduler(storage, lock_path, callback)
        await scheduler._tick(now=datetime.datetime.now() + datetime.timedelta(minutes=2))
        callback.assert_called_once_with("forever")

    @pytest.mark.asyncio
    async def test_missed_tasks_on_startup(self, storage, lock_path):
        callback = AsyncMock()
        task = storage.add(cron="* * * * *", prompt="missed", recurring=True, permanent=False)
        # Set last_fired_at to 5 minutes ago so it has missed fires
        storage.update_last_fired(task.id, datetime.datetime.now() - datetime.timedelta(minutes=5))

        scheduler = CronScheduler(storage, lock_path, callback)
        missed = scheduler.check_missed(now=datetime.datetime.now())
        assert len(missed) >= 1

    @pytest.mark.asyncio
    async def test_start_and_stop(self, storage, lock_path):
        callback = AsyncMock()
        scheduler = CronScheduler(storage, lock_path, callback)
        task = asyncio.create_task(scheduler.start(poll_interval=0.05))
        await asyncio.sleep(0.1)
        scheduler.stop()
        await task  # Should exit cleanly
