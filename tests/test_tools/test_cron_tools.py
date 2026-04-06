"""Tests for cron scheduling tools."""
from __future__ import annotations


import pytest

from llm_code.cron.storage import CronStorage
from llm_code.tools.base import PermissionLevel
from llm_code.tools.cron_create import CronCreateTool
from llm_code.tools.cron_list import CronListTool
from llm_code.tools.cron_delete import CronDeleteTool


@pytest.fixture
def storage(tmp_path):
    return CronStorage(tmp_path / ".llmcode" / "scheduled_tasks.json")


class TestCronCreateTool:
    def test_name(self):
        tool = CronCreateTool.__new__(CronCreateTool)
        assert tool.name == "cron_create"

    def test_permission(self):
        tool = CronCreateTool.__new__(CronCreateTool)
        assert tool.required_permission == PermissionLevel.WORKSPACE_WRITE

    def test_creates_task(self, storage):
        tool = CronCreateTool(storage)
        result = tool.execute({
            "cron": "0 9 * * *",
            "prompt": "good morning",
            "recurring": True,
            "permanent": False,
        })
        assert result.is_error is False
        assert "good morning" in result.output
        assert len(storage.list_all()) == 1

    def test_invalid_cron_expression(self, storage):
        tool = CronCreateTool(storage)
        result = tool.execute({
            "cron": "invalid",
            "prompt": "nope",
            "recurring": True,
            "permanent": False,
        })
        assert result.is_error is True

    def test_rejects_at_capacity(self, storage):
        for i in range(50):
            storage.add(cron="0 9 * * *", prompt=f"t{i}", recurring=True, permanent=False)
        tool = CronCreateTool(storage)
        result = tool.execute({
            "cron": "0 9 * * *",
            "prompt": "overflow",
            "recurring": True,
            "permanent": False,
        })
        assert result.is_error is True


class TestCronListTool:
    def test_name(self):
        tool = CronListTool.__new__(CronListTool)
        assert tool.name == "cron_list"

    def test_permission(self):
        tool = CronListTool.__new__(CronListTool)
        assert tool.required_permission == PermissionLevel.READ_ONLY

    def test_is_read_only(self):
        tool = CronListTool.__new__(CronListTool)
        assert tool.is_read_only({}) is True

    def test_lists_empty(self, storage):
        tool = CronListTool(storage)
        result = tool.execute({})
        assert result.is_error is False
        assert "No scheduled tasks" in result.output

    def test_lists_tasks(self, storage):
        storage.add(cron="0 9 * * *", prompt="morning", recurring=True, permanent=False)
        storage.add(cron="0 17 * * *", prompt="evening", recurring=True, permanent=True)
        tool = CronListTool(storage)
        result = tool.execute({})
        assert "morning" in result.output
        assert "evening" in result.output
        assert "permanent" in result.output.lower()


class TestCronDeleteTool:
    def test_name(self):
        tool = CronDeleteTool.__new__(CronDeleteTool)
        assert tool.name == "cron_delete"

    def test_permission(self):
        tool = CronDeleteTool.__new__(CronDeleteTool)
        assert tool.required_permission == PermissionLevel.WORKSPACE_WRITE

    def test_deletes_task(self, storage):
        task = storage.add(cron="0 9 * * *", prompt="bye", recurring=True, permanent=False)
        tool = CronDeleteTool(storage)
        result = tool.execute({"task_id": task.id})
        assert result.is_error is False
        assert len(storage.list_all()) == 0

    def test_not_found(self, storage):
        tool = CronDeleteTool(storage)
        result = tool.execute({"task_id": "nonexistent"})
        assert result.is_error is True
