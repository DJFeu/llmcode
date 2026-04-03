"""Tests for the task verifier."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from llm_code.task.types import TaskState, TaskStatus, VerifyResult
from llm_code.task.verifier import Verifier


@pytest.fixture
def cwd(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def verifier(cwd: Path) -> Verifier:
    return Verifier(cwd=cwd)


class TestPytestCheck:
    @patch("subprocess.run")
    def test_pytest_passes(self, mock_run: MagicMock, verifier: Verifier):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["python", "-m", "pytest"], returncode=0, stdout="6 passed", stderr=""
        )
        result = verifier.run_check_pytest()
        assert result.passed is True
        assert result.check_name == "pytest"
        assert "6 passed" in result.output

    @patch("subprocess.run")
    def test_pytest_fails(self, mock_run: MagicMock, verifier: Verifier):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["python", "-m", "pytest"], returncode=1, stdout="2 failed", stderr=""
        )
        result = verifier.run_check_pytest()
        assert result.passed is False

    @patch("subprocess.run")
    def test_pytest_timeout(self, mock_run: MagicMock, verifier: Verifier):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="pytest", timeout=60)
        result = verifier.run_check_pytest()
        assert result.passed is False
        assert "timeout" in result.output.lower()


class TestRuffCheck:
    @patch("subprocess.run")
    def test_ruff_passes(self, mock_run: MagicMock, verifier: Verifier):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["ruff", "check", "."], returncode=0, stdout="All checks passed", stderr=""
        )
        result = verifier.run_check_ruff()
        assert result.passed is True
        assert result.check_name == "ruff"

    @patch("subprocess.run")
    def test_ruff_fails(self, mock_run: MagicMock, verifier: Verifier):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["ruff", "check", "."], returncode=1, stdout="Found 3 errors", stderr=""
        )
        result = verifier.run_check_ruff()
        assert result.passed is False


class TestFileExistsCheck:
    def test_files_exist(self, verifier: Verifier, cwd: Path):
        (cwd / "main.py").write_text("print('hi')")
        result = verifier.run_check_files_exist(("main.py",))
        assert result.passed is True

    def test_files_missing(self, verifier: Verifier, cwd: Path):
        result = verifier.run_check_files_exist(("missing.py",))
        assert result.passed is False
        assert "missing.py" in result.output


class TestVerifyTask:
    @patch("subprocess.run")
    def test_verify_runs_all_checks(self, mock_run: MagicMock, verifier: Verifier, cwd: Path):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="ok", stderr=""
        )
        (cwd / "auth.py").write_text("pass")
        task = TaskState(
            id="t1",
            title="Build auth",
            status=TaskStatus.DO,
            files_modified=("auth.py",),
        )
        vr = verifier.verify(task)
        assert isinstance(vr, VerifyResult)
        assert vr.task_id == "t1"
        # Should have pytest + ruff + file_exists checks
        check_names = {c.check_name for c in vr.checks}
        assert "pytest" in check_names
        assert "ruff" in check_names
        assert "file_exists" in check_names

    @patch("subprocess.run")
    def test_verify_all_passed_true_when_all_pass(self, mock_run: MagicMock, verifier: Verifier, cwd: Path):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="ok", stderr=""
        )
        (cwd / "x.py").write_text("pass")
        task = TaskState(id="t2", title="Test", files_modified=("x.py",))
        vr = verifier.verify(task)
        assert vr.all_passed is True

    @patch("subprocess.run")
    def test_verify_all_passed_false_on_failure(self, mock_run: MagicMock, verifier: Verifier, cwd: Path):
        # First call (pytest) fails, second (ruff) passes
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=1, stdout="FAILED", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr=""),
        ]
        (cwd / "x.py").write_text("pass")
        task = TaskState(id="t3", title="Test", files_modified=("x.py",))
        vr = verifier.verify(task)
        assert vr.all_passed is False
