"""Tests for cron task storage."""
from __future__ import annotations

import datetime

import pytest

from llm_code.cron.storage import CronTask, CronStorage


class TestCronTask:
    def test_frozen(self):
        task = CronTask(
            id="t1",
            cron="*/5 * * * *",
            prompt="check status",
            recurring=True,
            permanent=False,
            created_at=datetime.datetime(2026, 4, 3, 10, 0),
        )
        with pytest.raises(AttributeError):
            task.id = "t2"  # type: ignore[misc]

    def test_defaults(self):
        task = CronTask(
            id="t1",
            cron="0 9 * * *",
            prompt="hello",
            recurring=True,
            permanent=False,
            created_at=datetime.datetime(2026, 4, 3, 10, 0),
        )
        assert task.last_fired_at is None


class TestCronStorage:
    def test_add_and_list(self, tmp_path):
        store = CronStorage(tmp_path / ".llm-code" / "scheduled_tasks.json")
        task = store.add(cron="0 9 * * *", prompt="morning check", recurring=True, permanent=False)
        assert task.id
        tasks = store.list_all()
        assert len(tasks) == 1
        assert tasks[0].prompt == "morning check"

    def test_persistence_across_instances(self, tmp_path):
        path = tmp_path / ".llm-code" / "scheduled_tasks.json"
        store1 = CronStorage(path)
        store1.add(cron="0 9 * * *", prompt="p1", recurring=True, permanent=False)
        store2 = CronStorage(path)
        assert len(store2.list_all()) == 1

    def test_remove(self, tmp_path):
        store = CronStorage(tmp_path / ".llm-code" / "scheduled_tasks.json")
        task = store.add(cron="0 9 * * *", prompt="p1", recurring=True, permanent=False)
        removed = store.remove(task.id)
        assert removed is True
        assert len(store.list_all()) == 0

    def test_remove_nonexistent(self, tmp_path):
        store = CronStorage(tmp_path / ".llm-code" / "scheduled_tasks.json")
        assert store.remove("nonexistent") is False

    def test_update_last_fired(self, tmp_path):
        store = CronStorage(tmp_path / ".llm-code" / "scheduled_tasks.json")
        task = store.add(cron="0 9 * * *", prompt="p1", recurring=True, permanent=False)
        fired_at = datetime.datetime(2026, 4, 3, 9, 0)
        updated = store.update_last_fired(task.id, fired_at)
        assert updated is not None
        assert updated.last_fired_at == fired_at
        # Verify persisted
        reloaded = CronStorage(tmp_path / ".llm-code" / "scheduled_tasks.json")
        assert reloaded.list_all()[0].last_fired_at == fired_at

    def test_max_50_tasks(self, tmp_path):
        store = CronStorage(tmp_path / ".llm-code" / "scheduled_tasks.json")
        for i in range(50):
            store.add(cron="0 9 * * *", prompt=f"task-{i}", recurring=True, permanent=False)
        with pytest.raises(ValueError, match="Maximum 50"):
            store.add(cron="0 9 * * *", prompt="overflow", recurring=True, permanent=False)

    def test_empty_file_loads_clean(self, tmp_path):
        path = tmp_path / ".llm-code" / "scheduled_tasks.json"
        store = CronStorage(path)
        assert store.list_all() == []
