"""Tests for background task indicator in status bar."""
from __future__ import annotations

from llm_code.tui.status_bar import StatusBar
from llm_code.task.manager import TaskLifecycleManager
from llm_code.task.types import TaskStatus


class TestBgTasksIndicator:
    def test_no_tasks_no_indicator(self):
        bar = StatusBar()
        bar.bg_tasks = 0
        content = bar._format_content()
        assert "task" not in content.lower() or "running" not in content.lower()

    def test_one_task_singular(self):
        bar = StatusBar()
        bar.bg_tasks = 1
        content = bar._format_content()
        assert "1 task running" in content

    def test_multiple_tasks_plural(self):
        bar = StatusBar()
        bar.bg_tasks = 3
        content = bar._format_content()
        assert "3 tasks running" in content


class TestRunningTaskCount:
    def test_no_tasks(self, tmp_path):
        mgr = TaskLifecycleManager(tmp_path / "tasks")
        assert mgr.running_task_count() == 0

    def test_counts_active_tasks(self, tmp_path):
        mgr = TaskLifecycleManager(tmp_path / "tasks")
        mgr.create_task("Task 1", "desc")  # stays PLAN (active)
        t2 = mgr.create_task("Task 2", "desc")
        t3 = mgr.create_task("Task 3", "desc")
        # t1 stays PLAN, t2 → DO, t3 → DO → VERIFY → CLOSE → DONE
        mgr.transition(t2.id, TaskStatus.DO)
        mgr.transition(t3.id, TaskStatus.DO)
        mgr.transition(t3.id, TaskStatus.VERIFY)
        mgr.transition(t3.id, TaskStatus.CLOSE)
        mgr.transition(t3.id, TaskStatus.DONE)
        # t1=PLAN (active), t2=DO (active), t3=DONE (not active)
        assert mgr.running_task_count() == 2

    def test_blocked_not_counted(self, tmp_path):
        mgr = TaskLifecycleManager(tmp_path / "tasks")
        task = mgr.create_task("Task 1", "desc")
        mgr.transition(task.id, TaskStatus.BLOCKED)
        assert mgr.running_task_count() == 0
