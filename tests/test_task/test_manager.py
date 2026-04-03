"""Tests for TaskLifecycleManager."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_code.task.manager import TaskLifecycleManager
from llm_code.task.types import TaskStatus


@pytest.fixture
def task_dir(tmp_path: Path) -> Path:
    d = tmp_path / "tasks"
    d.mkdir()
    return d


@pytest.fixture
def manager(task_dir: Path) -> TaskLifecycleManager:
    return TaskLifecycleManager(task_dir=task_dir)


class TestCreateTask:
    def test_creates_task_with_plan_status(self, manager: TaskLifecycleManager):
        task = manager.create_task(
            title="Build login page",
            plan="1. Create form\n2. Add validation",
            goals=("users can log in",),
            session_id="sess-1",
        )
        assert task.status == TaskStatus.PLAN
        assert task.title == "Build login page"
        assert task.plan == "1. Create form\n2. Add validation"
        assert task.goals == ("users can log in",)
        assert task.session_id == "sess-1"
        assert task.id != ""

    def test_creates_persists_to_disk(self, manager: TaskLifecycleManager, task_dir: Path):
        task = manager.create_task(title="Test task")
        file_path = task_dir / f"{task.id}.json"
        assert file_path.exists()
        data = json.loads(file_path.read_text())
        assert data["title"] == "Test task"

    def test_create_generates_unique_ids(self, manager: TaskLifecycleManager):
        t1 = manager.create_task(title="Task 1")
        t2 = manager.create_task(title="Task 2")
        assert t1.id != t2.id


class TestTransition:
    def test_plan_to_do(self, manager: TaskLifecycleManager):
        task = manager.create_task(title="Feature X")
        updated = manager.transition(task.id, TaskStatus.DO)
        assert updated.status == TaskStatus.DO
        assert updated.updated_at != task.updated_at

    def test_do_to_verify(self, manager: TaskLifecycleManager):
        task = manager.create_task(title="Feature X")
        manager.transition(task.id, TaskStatus.DO)
        updated = manager.transition(task.id, TaskStatus.VERIFY)
        assert updated.status == TaskStatus.VERIFY

    def test_verify_to_close(self, manager: TaskLifecycleManager):
        task = manager.create_task(title="Feature X")
        manager.transition(task.id, TaskStatus.DO)
        manager.transition(task.id, TaskStatus.VERIFY)
        updated = manager.transition(task.id, TaskStatus.CLOSE)
        assert updated.status == TaskStatus.CLOSE

    def test_close_to_done(self, manager: TaskLifecycleManager):
        task = manager.create_task(title="Feature X")
        manager.transition(task.id, TaskStatus.DO)
        manager.transition(task.id, TaskStatus.VERIFY)
        manager.transition(task.id, TaskStatus.CLOSE)
        updated = manager.transition(task.id, TaskStatus.DONE)
        assert updated.status == TaskStatus.DONE

    def test_any_to_blocked(self, manager: TaskLifecycleManager):
        task = manager.create_task(title="Feature X")
        manager.transition(task.id, TaskStatus.DO)
        updated = manager.transition(task.id, TaskStatus.BLOCKED)
        assert updated.status == TaskStatus.BLOCKED

    def test_blocked_to_do(self, manager: TaskLifecycleManager):
        task = manager.create_task(title="Feature X")
        manager.transition(task.id, TaskStatus.DO)
        manager.transition(task.id, TaskStatus.BLOCKED)
        updated = manager.transition(task.id, TaskStatus.DO)
        assert updated.status == TaskStatus.DO

    def test_verify_to_do_on_failure(self, manager: TaskLifecycleManager):
        """Verification failure can loop back to DO."""
        task = manager.create_task(title="Feature X")
        manager.transition(task.id, TaskStatus.DO)
        manager.transition(task.id, TaskStatus.VERIFY)
        updated = manager.transition(task.id, TaskStatus.DO)
        assert updated.status == TaskStatus.DO

    def test_invalid_transition_raises(self, manager: TaskLifecycleManager):
        task = manager.create_task(title="Feature X")
        with pytest.raises(ValueError, match="Invalid transition"):
            manager.transition(task.id, TaskStatus.CLOSE)  # plan -> close not allowed

    def test_transition_unknown_task_raises(self, manager: TaskLifecycleManager):
        with pytest.raises(KeyError):
            manager.transition("nonexistent", TaskStatus.DO)

    def test_transition_persists(self, manager: TaskLifecycleManager, task_dir: Path):
        task = manager.create_task(title="Feature X")
        manager.transition(task.id, TaskStatus.DO)
        data = json.loads((task_dir / f"{task.id}.json").read_text())
        assert data["status"] == "do"


class TestGetTask:
    def test_get_existing(self, manager: TaskLifecycleManager):
        task = manager.create_task(title="My task")
        retrieved = manager.get_task(task.id)
        assert retrieved is not None
        assert retrieved.id == task.id

    def test_get_nonexistent_returns_none(self, manager: TaskLifecycleManager):
        assert manager.get_task("no-such-task") is None


class TestListTasks:
    def test_empty(self, manager: TaskLifecycleManager):
        assert manager.list_tasks() == ()

    def test_lists_all(self, manager: TaskLifecycleManager):
        manager.create_task(title="A")
        manager.create_task(title="B")
        tasks = manager.list_tasks()
        assert len(tasks) == 2

    def test_filter_by_status(self, manager: TaskLifecycleManager):
        t1 = manager.create_task(title="A")
        manager.create_task(title="B")
        manager.transition(t1.id, TaskStatus.DO)
        doing = manager.list_tasks(status=TaskStatus.DO)
        assert len(doing) == 1
        assert doing[0].id == t1.id

    def test_list_incomplete(self, manager: TaskLifecycleManager):
        t1 = manager.create_task(title="A")
        t2 = manager.create_task(title="B")
        manager.transition(t1.id, TaskStatus.DO)
        manager.transition(t1.id, TaskStatus.VERIFY)
        manager.transition(t1.id, TaskStatus.CLOSE)
        manager.transition(t1.id, TaskStatus.DONE)
        incomplete = manager.list_tasks(exclude_done=True)
        assert len(incomplete) == 1
        assert incomplete[0].id == t2.id


class TestUpdateFields:
    def test_update_files_modified(self, manager: TaskLifecycleManager):
        task = manager.create_task(title="Feature X")
        manager.transition(task.id, TaskStatus.DO)
        updated = manager.update_task(task.id, files_modified=("auth.py", "test_auth.py"))
        assert updated.files_modified == ("auth.py", "test_auth.py")

    def test_update_plan(self, manager: TaskLifecycleManager):
        task = manager.create_task(title="Feature X")
        updated = manager.update_task(task.id, plan="New plan")
        assert updated.plan == "New plan"

    def test_append_verify_result(self, manager: TaskLifecycleManager):
        from llm_code.task.types import CheckResult, VerifyResult
        task = manager.create_task(title="Feature X")
        vr = VerifyResult(
            task_id=task.id,
            all_passed=True,
            checks=(CheckResult(check_name="pytest", passed=True, output="ok"),),
        )
        updated = manager.append_verify_result(task.id, vr)
        assert len(updated.verify_results) == 1
