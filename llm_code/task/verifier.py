"""Verifier: run automatic checks (pytest, ruff, file_exists) for a task."""
from __future__ import annotations

import subprocess
from pathlib import Path

from llm_code.task.types import CheckResult, TaskState, VerifyResult


class Verifier:
    """Runs automated verification checks against a task's output."""

    def __init__(self, cwd: Path, timeout: int = 120) -> None:
        self._cwd = cwd
        self._timeout = timeout

    def verify(self, task: TaskState) -> VerifyResult:
        """Run all applicable checks for a task and return a VerifyResult."""
        checks: list[CheckResult] = []

        # Always run pytest and ruff
        checks.append(self.run_check_pytest())
        checks.append(self.run_check_ruff())

        # Check that modified files exist
        if task.files_modified:
            checks.append(self.run_check_files_exist(task.files_modified))

        all_passed = all(c.passed for c in checks)

        return VerifyResult(
            task_id=task.id,
            all_passed=all_passed,
            checks=tuple(checks),
            llm_judgment="",  # filled by LLM in a separate step
            recommended_action="continue" if all_passed else "replan",
        )

    def run_check_pytest(self) -> CheckResult:
        """Run pytest and return a CheckResult."""
        try:
            result = subprocess.run(
                ["python", "-m", "pytest", "--tb=short", "-q"],
                cwd=str(self._cwd),
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
            return CheckResult(
                check_name="pytest",
                passed=result.returncode == 0,
                output=(result.stdout + result.stderr).strip()[:2000],
            )
        except subprocess.TimeoutExpired:
            return CheckResult(
                check_name="pytest",
                passed=False,
                output=f"Timeout after {self._timeout}s",
            )
        except FileNotFoundError:
            return CheckResult(
                check_name="pytest",
                passed=False,
                output="pytest not found in PATH",
            )

    def run_check_ruff(self) -> CheckResult:
        """Run ruff check and return a CheckResult."""
        try:
            result = subprocess.run(
                ["ruff", "check", "."],
                cwd=str(self._cwd),
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
            return CheckResult(
                check_name="ruff",
                passed=result.returncode == 0,
                output=(result.stdout + result.stderr).strip()[:2000],
            )
        except subprocess.TimeoutExpired:
            return CheckResult(
                check_name="ruff",
                passed=False,
                output=f"Timeout after {self._timeout}s",
            )
        except FileNotFoundError:
            return CheckResult(
                check_name="ruff",
                passed=False,
                output="ruff not found in PATH",
            )

    def run_check_files_exist(self, files: tuple[str, ...]) -> CheckResult:
        """Check that all modified files exist on disk."""
        missing = [f for f in files if not (self._cwd / f).exists()]
        if missing:
            return CheckResult(
                check_name="file_exists",
                passed=False,
                output=f"Missing files: {', '.join(missing)}",
            )
        return CheckResult(check_name="file_exists", passed=True, output="All files present")
