"""Frozen dataclasses for the task lifecycle."""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from enum import Enum


class TaskStatus(Enum):
    PLAN = "plan"
    DO = "do"
    VERIFY = "verify"
    CLOSE = "close"
    DONE = "done"
    BLOCKED = "blocked"


@dataclasses.dataclass(frozen=True)
class CheckResult:
    check_name: str
    passed: bool
    output: str = ""


@dataclasses.dataclass(frozen=True)
class VerifyResult:
    task_id: str
    all_passed: bool
    checks: tuple[CheckResult, ...] = ()
    llm_judgment: str = ""
    recommended_action: str = "continue"  # "continue" | "replan" | "escalate"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclasses.dataclass(frozen=True)
class TaskState:
    id: str
    title: str
    status: TaskStatus = TaskStatus.PLAN
    plan: str = ""
    goals: tuple[str, ...] = ()
    files_modified: tuple[str, ...] = ()
    verify_results: tuple[VerifyResult, ...] = ()
    diagnostic_path: str = ""
    created_at: str = dataclasses.field(default_factory=_now_iso)
    updated_at: str = dataclasses.field(default_factory=_now_iso)
    session_id: str = ""

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict."""
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status.value,
            "plan": self.plan,
            "goals": list(self.goals),
            "files_modified": list(self.files_modified),
            "verify_results": [
                {
                    "task_id": vr.task_id,
                    "all_passed": vr.all_passed,
                    "checks": [
                        {"check_name": c.check_name, "passed": c.passed, "output": c.output}
                        for c in vr.checks
                    ],
                    "llm_judgment": vr.llm_judgment,
                    "recommended_action": vr.recommended_action,
                }
                for vr in self.verify_results
            ],
            "diagnostic_path": self.diagnostic_path,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "session_id": self.session_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TaskState:
        """Deserialize from a dict."""
        verify_results = tuple(
            VerifyResult(
                task_id=vr["task_id"],
                all_passed=vr["all_passed"],
                checks=tuple(
                    CheckResult(
                        check_name=c["check_name"],
                        passed=c["passed"],
                        output=c.get("output", ""),
                    )
                    for c in vr.get("checks", [])
                ),
                llm_judgment=vr.get("llm_judgment", ""),
                recommended_action=vr.get("recommended_action", "continue"),
            )
            for vr in data.get("verify_results", [])
        )
        return cls(
            id=data["id"],
            title=data["title"],
            status=TaskStatus(data["status"]),
            plan=data.get("plan", ""),
            goals=tuple(data.get("goals", [])),
            files_modified=tuple(data.get("files_modified", [])),
            verify_results=verify_results,
            diagnostic_path=data.get("diagnostic_path", ""),
            created_at=data.get("created_at", _now_iso()),
            updated_at=data.get("updated_at", _now_iso()),
            session_id=data.get("session_id", ""),
        )
