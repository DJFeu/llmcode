"""Tests for task lifecycle tools."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from llm_code.task.diagnostics import DiagnosticsEngine
from llm_code.task.manager import TaskLifecycleManager
from llm_code.task.types import TaskStatus
from llm_code.task.verifier import Verifier
from llm_code.tools.task_close import TaskCloseTool
from llm_code.tools.task_plan import TaskPlanTool
from llm_code.tools.task_verify import TaskVerifyTool


@pytest.fixture
def task_dir(tmp_path: Path) -> Path:
    d = tmp_path / "tasks"
    d.mkdir()
    return d


@pytest.fixture
def diag_dir(tmp_path: Path) -> Path:
    d = tmp_path / "diagnostics"
    d.mkdir()
    return d


@pytest.fixture
def manager(task_dir: Path) -> TaskLifecycleManager:
    return TaskLifecycleManager(task_dir=task_dir)


@pytest.fixture
def verifier(tmp_path: Path) -> Verifier:
    return Verifier(cwd=tmp_path)


@pytest.fixture
def diagnostics(diag_dir: Path) -> DiagnosticsEngine:
    return DiagnosticsEngine(diagnostics_dir=diag_dir)


class TestTaskPlanTool:
    def test_name(self, manager: TaskLifecycleManager):
        tool = TaskPlanTool(manager, session_id="s1")
        assert tool.name == "task_plan"

    def test_creates_task(self, manager: TaskLifecycleManager):
        tool = TaskPlanTool(manager, session_id="s1")
        result = tool.execute({
            "title": "Implement login",
            "plan": "1. Create form\n2. Validate input",
            "goals": ["users can log in", "session persists"],
        })
        assert not result.is_error
        assert "task-" in result.output
        tasks = manager.list_tasks()
        assert len(tasks) == 1
        assert tasks[0].title == "Implement login"
        assert tasks[0].session_id == "s1"

    def test_creates_task_minimal(self, manager: TaskLifecycleManager):
        tool = TaskPlanTool(manager, session_id="s1")
        result = tool.execute({"title": "Quick fix"})
        assert not result.is_error

    def test_missing_title_is_error(self, manager: TaskLifecycleManager):
        tool = TaskPlanTool(manager, session_id="s1")
        result = tool.execute({})
        assert result.is_error


class TestTaskVerifyTool:
    @patch("subprocess.run")
    def test_verifies_task(
        self,
        mock_run: MagicMock,
        manager: TaskLifecycleManager,
        verifier: Verifier,
        diagnostics: DiagnosticsEngine,
        tmp_path: Path,
    ):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="ok", stderr=""
        )
        (tmp_path / "auth.py").write_text("pass")
        task = manager.create_task(title="Build auth")
        manager.transition(task.id, TaskStatus.DO)
        manager.update_task(task.id, files_modified=("auth.py",))

        tool = TaskVerifyTool(manager, verifier, diagnostics)
        result = tool.execute({"task_id": task.id})
        assert not result.is_error
        assert "passed" in result.output.lower() or "continue" in result.output.lower()

    def test_unknown_task_is_error(
        self,
        manager: TaskLifecycleManager,
        verifier: Verifier,
        diagnostics: DiagnosticsEngine,
    ):
        tool = TaskVerifyTool(manager, verifier, diagnostics)
        result = tool.execute({"task_id": "nonexistent"})
        assert result.is_error

    def test_name(
        self,
        manager: TaskLifecycleManager,
        verifier: Verifier,
        diagnostics: DiagnosticsEngine,
    ):
        tool = TaskVerifyTool(manager, verifier, diagnostics)
        assert tool.name == "task_verify"


class TestTaskCloseTool:
    def test_name(self, manager: TaskLifecycleManager):
        tool = TaskCloseTool(manager)
        assert tool.name == "task_close"

    def test_closes_task(self, manager: TaskLifecycleManager):
        task = manager.create_task(title="Feature X")
        manager.transition(task.id, TaskStatus.DO)
        manager.transition(task.id, TaskStatus.VERIFY)
        manager.transition(task.id, TaskStatus.CLOSE)

        tool = TaskCloseTool(manager)
        result = tool.execute({"task_id": task.id, "summary": "Completed login feature"})
        assert not result.is_error
        closed = manager.get_task(task.id)
        assert closed is not None
        assert closed.status == TaskStatus.DONE

    def test_close_unknown_task_is_error(self, manager: TaskLifecycleManager):
        tool = TaskCloseTool(manager)
        result = tool.execute({"task_id": "nonexistent", "summary": "done"})
        assert result.is_error

    def test_close_requires_close_status(self, manager: TaskLifecycleManager):
        """Task must be in CLOSE status to finalize to DONE."""
        task = manager.create_task(title="Feature X")
        tool = TaskCloseTool(manager)
        result = tool.execute({"task_id": task.id, "summary": "done"})
        assert result.is_error
        assert "Invalid transition" in result.output or "invalid" in result.output.lower()
