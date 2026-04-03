"""Tests for the diagnostics engine."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_code.task.diagnostics import DiagnosticsEngine, DiagnosticReport
from llm_code.task.types import CheckResult, TaskState, VerifyResult


@pytest.fixture
def diag_dir(tmp_path: Path) -> Path:
    d = tmp_path / "diagnostics"
    d.mkdir()
    return d


@pytest.fixture
def engine(diag_dir: Path) -> DiagnosticsEngine:
    return DiagnosticsEngine(diagnostics_dir=diag_dir)


def _make_verify_result(
    task_id: str = "t1",
    passed: bool = True,
    checks: tuple[CheckResult, ...] = (),
) -> VerifyResult:
    return VerifyResult(
        task_id=task_id,
        all_passed=passed,
        checks=checks,
        recommended_action="continue" if passed else "replan",
    )


class TestDiagnosticReport:
    def test_frozen(self):
        report = DiagnosticReport(
            task_id="t1",
            failed_checks=("pytest",),
            recommendation="replan",
            summary="Tests failed",
            report_path="",
        )
        with pytest.raises(AttributeError):
            report.recommendation = "escalate"  # type: ignore[misc]


class TestAnalyze:
    def test_all_passed_returns_continue(self, engine: DiagnosticsEngine):
        vr = _make_verify_result(passed=True)
        task = TaskState(id="t1", title="Test")
        report = engine.analyze(task, vr)
        assert report.recommendation == "continue"
        assert report.failed_checks == ()

    def test_single_failure_returns_replan(self, engine: DiagnosticsEngine):
        checks = (
            CheckResult(check_name="pytest", passed=False, output="2 failed"),
            CheckResult(check_name="ruff", passed=True, output="ok"),
        )
        vr = _make_verify_result(passed=False, checks=checks)
        task = TaskState(id="t1", title="Test")
        report = engine.analyze(task, vr)
        assert report.recommendation == "replan"
        assert "pytest" in report.failed_checks

    def test_all_checks_fail_returns_escalate(self, engine: DiagnosticsEngine):
        checks = (
            CheckResult(check_name="pytest", passed=False, output="error"),
            CheckResult(check_name="ruff", passed=False, output="error"),
            CheckResult(check_name="file_exists", passed=False, output="missing"),
        )
        vr = _make_verify_result(passed=False, checks=checks)
        task = TaskState(id="t1", title="Test")
        report = engine.analyze(task, vr)
        assert report.recommendation == "escalate"

    def test_repeated_failures_escalate(self, engine: DiagnosticsEngine):
        """If a task has multiple prior verify_results that all failed, escalate."""
        checks = (CheckResult(check_name="pytest", passed=False, output="fail"),)
        vr1 = _make_verify_result(task_id="t1", passed=False, checks=checks)
        vr2 = _make_verify_result(task_id="t1", passed=False, checks=checks)
        vr_current = _make_verify_result(task_id="t1", passed=False, checks=checks)
        task = TaskState(
            id="t1",
            title="Test",
            verify_results=(vr1, vr2),  # two prior failures
        )
        report = engine.analyze(task, vr_current)
        assert report.recommendation == "escalate"

    def test_saves_report_to_disk(self, engine: DiagnosticsEngine, diag_dir: Path):
        checks = (CheckResult(check_name="pytest", passed=False, output="fail"),)
        vr = _make_verify_result(passed=False, checks=checks)
        task = TaskState(id="t1", title="Test")
        report = engine.analyze(task, vr)
        assert report.report_path != ""
        assert Path(report.report_path).exists()
        data = json.loads(Path(report.report_path).read_text())
        assert data["task_id"] == "t1"
