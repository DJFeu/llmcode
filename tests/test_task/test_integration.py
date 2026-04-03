"""Integration test: full task lifecycle plan -> do -> verify -> close -> done."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from llm_code.task.diagnostics import DiagnosticsEngine
from llm_code.task.manager import TaskLifecycleManager, build_incomplete_tasks_prompt
from llm_code.task.types import TaskStatus
from llm_code.task.verifier import Verifier
from llm_code.tools.task_close import TaskCloseTool
from llm_code.tools.task_plan import TaskPlanTool
from llm_code.tools.task_verify import TaskVerifyTool


@pytest.fixture
def workspace(tmp_path: Path):
    task_dir = tmp_path / "tasks"
    diag_dir = tmp_path / "diagnostics"
    task_dir.mkdir()
    diag_dir.mkdir()
    return {
        "cwd": tmp_path,
        "task_dir": task_dir,
        "diag_dir": diag_dir,
        "manager": TaskLifecycleManager(task_dir=task_dir),
        "verifier": Verifier(cwd=tmp_path),
        "diagnostics": DiagnosticsEngine(diagnostics_dir=diag_dir),
    }


class TestFullLifecycle:
    @patch("subprocess.run")
    def test_plan_do_verify_close_done(self, mock_run: MagicMock, workspace: dict):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="all passed", stderr=""
        )
        mgr = workspace["manager"]
        cwd = workspace["cwd"]

        # 1. Plan
        plan_tool = TaskPlanTool(mgr, session_id="integration-test")
        result = plan_tool.execute({
            "title": "Add user auth",
            "plan": "1. Create user model\n2. Add login endpoint",
            "goals": ["users can log in", "tokens are issued"],
        })
        assert not result.is_error
        task_id = mgr.list_tasks()[0].id

        # 2. Do (transition)
        mgr.transition(task_id, TaskStatus.DO)
        (cwd / "auth.py").write_text("def login(): pass")
        mgr.update_task(task_id, files_modified=("auth.py",))

        # 3. Verify
        mgr.transition(task_id, TaskStatus.VERIFY)
        verify_tool = TaskVerifyTool(mgr, workspace["verifier"], workspace["diagnostics"])
        result = verify_tool.execute({"task_id": task_id})
        assert not result.is_error
        assert "PASSED" in result.output

        # 4. Close
        mgr.transition(task_id, TaskStatus.CLOSE)
        close_tool = TaskCloseTool(mgr)
        result = close_tool.execute({"task_id": task_id, "summary": "Auth system complete"})
        assert not result.is_error

        # 5. Verify final state
        task = mgr.get_task(task_id)
        assert task is not None
        assert task.status == TaskStatus.DONE

    def test_cross_session_prompt_injection(self, workspace: dict):
        mgr = workspace["manager"]

        # Session 1: create task, leave in DO
        task = mgr.create_task(title="Unfinished work", session_id="session-1")
        mgr.transition(task.id, TaskStatus.DO)

        # Session 2: check prompt injection
        prompt_section = build_incomplete_tasks_prompt(mgr)
        assert "Unfinished work" in prompt_section
        assert task.id in prompt_section
        assert "do" in prompt_section.lower()

    @patch("subprocess.run")
    def test_verify_failure_triggers_diagnostics(self, mock_run: MagicMock, workspace: dict):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="FAILED", stderr=""
        )
        mgr = workspace["manager"]
        cwd = workspace["cwd"]

        task = mgr.create_task(title="Buggy feature")
        mgr.transition(task.id, TaskStatus.DO)
        (cwd / "buggy.py").write_text("pass")
        mgr.update_task(task.id, files_modified=("buggy.py",))
        mgr.transition(task.id, TaskStatus.VERIFY)

        verify_tool = TaskVerifyTool(mgr, workspace["verifier"], workspace["diagnostics"])
        result = verify_tool.execute({"task_id": task.id})
        assert not result.is_error  # tool itself succeeds
        assert "FAILED" in result.output
        assert "replan" in result.output.lower() or "escalate" in result.output.lower()
