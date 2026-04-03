"""Tests for incomplete task injection into system prompt."""
from __future__ import annotations

from pathlib import Path

import pytest

from llm_code.task.manager import TaskLifecycleManager, build_incomplete_tasks_prompt
from llm_code.task.types import TaskStatus


@pytest.fixture
def task_dir(tmp_path: Path) -> Path:
    d = tmp_path / "tasks"
    d.mkdir()
    return d


@pytest.fixture
def manager(task_dir: Path) -> TaskLifecycleManager:
    return TaskLifecycleManager(task_dir=task_dir)


class TestBuildIncompleteTasksSection:
    def test_no_tasks_returns_empty(self, manager: TaskLifecycleManager):
        section = build_incomplete_tasks_prompt(manager)
        assert section == ""

    def test_done_tasks_excluded(self, manager: TaskLifecycleManager):
        t = manager.create_task(title="Done task")
        manager.transition(t.id, TaskStatus.DO)
        manager.transition(t.id, TaskStatus.VERIFY)
        manager.transition(t.id, TaskStatus.CLOSE)
        manager.transition(t.id, TaskStatus.DONE)
        section = build_incomplete_tasks_prompt(manager)
        assert section == ""

    def test_incomplete_tasks_included(self, manager: TaskLifecycleManager):
        manager.create_task(title="In progress task")
        section = build_incomplete_tasks_prompt(manager)
        assert "In progress task" in section
        assert "plan" in section.lower()

    def test_multiple_incomplete_tasks(self, manager: TaskLifecycleManager):
        manager.create_task(title="Task A")
        t2 = manager.create_task(title="Task B")
        manager.transition(t2.id, TaskStatus.DO)
        section = build_incomplete_tasks_prompt(manager)
        assert "Task A" in section
        assert "Task B" in section
