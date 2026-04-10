"""Consolidated task lifecycle tools — plan, verify, close.

All task tool classes live here. The original per-file modules
(task_plan, task_verify, task_close) re-export from this file
for backward compatibility.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from llm_code.task.manager import TaskLifecycleManager
from llm_code.task.types import TaskStatus
from llm_code.tools.base import PermissionLevel, Tool, ToolResult

if TYPE_CHECKING:
    from llm_code.task.diagnostics import DiagnosticsEngine
    from llm_code.task.verifier import Verifier


# ---------------------------------------------------------------------------
# TaskPlan
# ---------------------------------------------------------------------------

class TaskPlanInput(BaseModel):
    title: str
    plan: str = ""
    goals: list[str] = []


class TaskPlanTool(Tool):
    """Create a new structured task with a plan and goals."""

    def __init__(self, manager: TaskLifecycleManager, session_id: str = "") -> None:
        self._manager = manager
        self._session_id = session_id

    @property
    def name(self) -> str:
        return "task_plan"

    @property
    def description(self) -> str:
        return (
            "Create a new structured task. Provide a title, an implementation plan, "
            "and measurable goals. The task starts in PLAN status."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short task title"},
                "plan": {"type": "string", "description": "Step-by-step implementation plan"},
                "goals": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Measurable completion goals",
                },
            },
            "required": ["title"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.WORKSPACE_WRITE

    @property
    def input_model(self) -> type[TaskPlanInput]:
        return TaskPlanInput

    def execute(self, args: dict) -> ToolResult:
        title = args.get("title", "").strip()
        if not title:
            return ToolResult(output="Error: title is required", is_error=True)

        plan = args.get("plan", "")
        goals = tuple(args.get("goals", []))

        task = self._manager.create_task(
            title=title,
            plan=plan,
            goals=goals,
            session_id=self._session_id,
        )
        return ToolResult(
            output=(
                f"Created task {task.id}: {task.title}\n"
                f"Status: {task.status.value}\n"
                f"Goals: {', '.join(task.goals) if task.goals else '(none)'}\n"
                f"Plan:\n{task.plan or '(no plan set)'}"
            )
        )


# ---------------------------------------------------------------------------
# TaskVerify
# ---------------------------------------------------------------------------

class TaskVerifyInput(BaseModel):
    task_id: str


class TaskVerifyTool(Tool):
    """Run automated verification checks (pytest, ruff, file_exists) on a task."""

    def __init__(
        self,
        manager: TaskLifecycleManager,
        verifier: "Verifier",
        diagnostics: "DiagnosticsEngine",
    ) -> None:
        self._manager = manager
        self._verifier = verifier
        self._diagnostics = diagnostics

    @property
    def name(self) -> str:
        return "task_verify"

    @property
    def description(self) -> str:
        return (
            "Run automated verification checks on a task: pytest, ruff, and file_exists. "
            "Returns check results and a recommended action (continue/replan/escalate)."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The task ID to verify"},
            },
            "required": ["task_id"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    @property
    def input_model(self) -> type[TaskVerifyInput]:
        return TaskVerifyInput

    def is_read_only(self, args: dict) -> bool:
        return True

    def execute(self, args: dict) -> ToolResult:
        task_id = args["task_id"]
        task = self._manager.get_task(task_id)
        if task is None:
            return ToolResult(output=f"Task not found: {task_id}", is_error=True)

        # Run automated checks
        verify_result = self._verifier.verify(task)

        # Append result to task history
        self._manager.append_verify_result(task_id, verify_result)

        # Run diagnostics
        report = self._diagnostics.analyze(task, verify_result)

        # Format output
        lines = [f"Verification for task {task_id}: {task.title}"]
        lines.append(f"Overall: {'PASSED' if verify_result.all_passed else 'FAILED'}")
        lines.append("")
        for check in verify_result.checks:
            icon = "PASS" if check.passed else "FAIL"
            lines.append(f"  [{icon}] {check.check_name}: {check.output[:200]}")
        lines.append("")
        lines.append(f"Recommendation: {report.recommendation}")
        if report.summary:
            lines.append(f"Diagnostic: {report.summary}")
        if report.report_path:
            lines.append(f"Full report: {report.report_path}")

        return ToolResult(output="\n".join(lines))


# ---------------------------------------------------------------------------
# TaskClose
# ---------------------------------------------------------------------------

class TaskCloseInput(BaseModel):
    task_id: str
    summary: str = ""


class TaskCloseTool(Tool):
    """Close a task: transition to DONE and write a completion summary."""

    def __init__(self, manager: TaskLifecycleManager) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "task_close"

    @property
    def description(self) -> str:
        return (
            "Close a completed task. Transitions from CLOSE to DONE and writes "
            "a completion summary. The task must be in CLOSE status."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The task ID to close"},
                "summary": {"type": "string", "description": "Completion summary"},
            },
            "required": ["task_id"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.WORKSPACE_WRITE

    @property
    def input_model(self) -> type[TaskCloseInput]:
        return TaskCloseInput

    def execute(self, args: dict) -> ToolResult:
        task_id = args["task_id"]
        summary = args.get("summary", "")

        task = self._manager.get_task(task_id)
        if task is None:
            return ToolResult(output=f"Task not found: {task_id}", is_error=True)

        try:
            self._manager.transition(task_id, TaskStatus.DONE)
        except ValueError as exc:
            return ToolResult(output=str(exc), is_error=True)

        # Write summary to task
        if summary:
            self._manager.update_task(task_id, plan=f"{task.plan}\n\n## Summary\n{summary}")

        closed = self._manager.get_task(task_id)
        files = ", ".join(closed.files_modified) if closed and closed.files_modified else "(none)"

        return ToolResult(
            output=(
                f"Task {task_id} closed successfully.\n"
                f"Title: {task.title}\n"
                f"Files modified: {files}\n"
                f"Summary: {summary or '(no summary)'}"
            )
        )
