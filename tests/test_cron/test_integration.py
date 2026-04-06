"""Integration tests for the cron scheduling feature."""
from __future__ import annotations

import datetime

import pytest

from llm_code.cron.parser import parse_cron, next_fire_time
from llm_code.cron.scheduler import CronScheduler
from llm_code.cron.storage import CronStorage


class TestCronLifecycle:
    @pytest.mark.asyncio
    async def test_create_fire_expire(self, tmp_path):
        """Full lifecycle: create task -> fire it -> verify fired -> auto-expire."""
        storage = CronStorage(tmp_path / ".llmcode" / "scheduled_tasks.json")
        lock_path = tmp_path / ".llmcode" / "cron.lock"
        fired_prompts: list[str] = []

        async def on_fire(prompt: str) -> None:
            fired_prompts.append(prompt)

        # Create a recurring non-permanent task
        task = storage.add(cron="* * * * *", prompt="integration test", recurring=True, permanent=False)
        assert len(storage.list_all()) == 1

        # Fire it
        scheduler = CronScheduler(storage, lock_path, on_fire)
        await scheduler._tick(now=datetime.datetime.now() + datetime.timedelta(minutes=2))
        assert fired_prompts == ["integration test"]
        assert storage.list_all()[0].last_fired_at is not None

        # Delete it
        storage.remove(task.id)
        assert len(storage.list_all()) == 0

    @pytest.mark.asyncio
    async def test_one_shot_task_removed(self, tmp_path):
        """A non-recurring task fires once then is removed."""
        storage = CronStorage(tmp_path / ".llmcode" / "scheduled_tasks.json")
        lock_path = tmp_path / ".llmcode" / "cron.lock"
        fired: list[str] = []

        async def on_fire(prompt: str) -> None:
            fired.append(prompt)

        storage.add(cron="* * * * *", prompt="once only", recurring=False, permanent=False)
        scheduler = CronScheduler(storage, lock_path, on_fire)
        await scheduler._tick(now=datetime.datetime.now() + datetime.timedelta(minutes=2))
        assert fired == ["once only"]
        assert len(storage.list_all()) == 0

    def test_parser_roundtrip(self):
        """Parse -> next_fire_time -> verify the result matches the expression."""
        expr = parse_cron("30 9 * * 1")
        after = datetime.datetime(2026, 4, 3, 10, 0)  # Thursday
        nxt = next_fire_time(expr, after)
        assert nxt.minute == 30
        assert nxt.hour == 9
        assert nxt.weekday() == 0  # Monday
