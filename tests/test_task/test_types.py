"""Tests for task lifecycle types."""
from __future__ import annotations

import pytest

from llm_code.task.types import (
    CheckResult,
    TaskState,
    TaskStatus,
    VerifyResult,
)


class TestTaskStatus:
    def test_has_expected_values(self):
        assert TaskStatus.PLAN.value == "plan"
        assert TaskStatus.DO.value == "do"
        assert TaskStatus.VERIFY.value == "verify"
        assert TaskStatus.CLOSE.value == "close"
        assert TaskStatus.DONE.value == "done"
        assert TaskStatus.BLOCKED.value == "blocked"

    def test_all_statuses_count(self):
        # Updated for H5a (Sprint 3) — PENDING_APPROVAL added alongside
        # the six legacy states. Old session files keep round-tripping
        # because the original six enum values are preserved.
        assert len(TaskStatus) == 7


class TestCheckResult:
    def test_frozen(self):
        cr = CheckResult(check_name="pytest", passed=True, output="All passed")
        with pytest.raises(AttributeError):
            cr.passed = False  # type: ignore[misc]

    def test_fields(self):
        cr = CheckResult(check_name="ruff", passed=False, output="E501 line too long")
        assert cr.check_name == "ruff"
        assert cr.passed is False
        assert cr.output == "E501 line too long"

    def test_default_output(self):
        cr = CheckResult(check_name="file_exists", passed=True)
        assert cr.output == ""


class TestVerifyResult:
    def test_frozen(self):
        vr = VerifyResult(
            task_id="t1",
            all_passed=True,
            checks=(),
            llm_judgment="",
            recommended_action="continue",
        )
        with pytest.raises(AttributeError):
            vr.all_passed = False  # type: ignore[misc]

    def test_fields_with_checks(self):
        c1 = CheckResult(check_name="pytest", passed=True, output="ok")
        c2 = CheckResult(check_name="ruff", passed=False, output="error")
        vr = VerifyResult(
            task_id="t1",
            all_passed=False,
            checks=(c1, c2),
            llm_judgment="ruff failed on style",
            recommended_action="continue",
        )
        assert len(vr.checks) == 2
        assert vr.all_passed is False
        assert vr.recommended_action == "continue"


class TestTaskState:
    def test_frozen(self):
        ts = TaskState(
            id="task-001",
            title="Implement login",
            status=TaskStatus.PLAN,
            plan="Step 1: ...",
            goals=("user can log in",),
            files_modified=(),
            verify_results=(),
            diagnostic_path="",
            created_at="2026-04-03T00:00:00Z",
            updated_at="2026-04-03T00:00:00Z",
            session_id="sess-abc",
        )
        with pytest.raises(AttributeError):
            ts.status = TaskStatus.DO  # type: ignore[misc]

    def test_default_fields(self):
        ts = TaskState(
            id="task-002",
            title="Add tests",
        )
        assert ts.status == TaskStatus.PLAN
        assert ts.plan == ""
        assert ts.goals == ()
        assert ts.files_modified == ()
        assert ts.verify_results == ()
        assert ts.diagnostic_path == ""
        assert ts.session_id == ""
        assert ts.created_at != ""
        assert ts.updated_at != ""

    def test_to_dict_roundtrip(self):
        ts = TaskState(
            id="task-003",
            title="Refactor auth",
            status=TaskStatus.DO,
            goals=("clean code", "no regressions"),
            files_modified=("auth.py",),
        )
        d = ts.to_dict()
        assert d["id"] == "task-003"
        assert d["status"] == "do"
        assert d["goals"] == ["clean code", "no regressions"]
        restored = TaskState.from_dict(d)
        assert restored == ts

    def test_from_dict_with_verify_results(self):
        d = {
            "id": "t4",
            "title": "test",
            "status": "verify",
            "plan": "",
            "goals": [],
            "files_modified": [],
            "verify_results": [
                {
                    "task_id": "t4",
                    "all_passed": True,
                    "checks": [{"check_name": "pytest", "passed": True, "output": "ok"}],
                    "llm_judgment": "",
                    "recommended_action": "continue",
                }
            ],
            "diagnostic_path": "",
            "created_at": "2026-04-03T00:00:00Z",
            "updated_at": "2026-04-03T00:00:00Z",
            "session_id": "s1",
        }
        ts = TaskState.from_dict(d)
        assert ts.status == TaskStatus.VERIFY
        assert len(ts.verify_results) == 1
        assert ts.verify_results[0].checks[0].check_name == "pytest"
