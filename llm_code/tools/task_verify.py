"""TaskVerifyTool: run verification checks on a task."""
from __future__ import annotations

from pydantic import BaseModel

from llm_code.task.diagnostics import DiagnosticsEngine
from llm_code.task.manager import TaskLifecycleManager
from llm_code.task.verifier import Verifier
from llm_code.tools.base import PermissionLevel, Tool, ToolResult


class TaskVerifyInput(BaseModel):
    task_id: str


class TaskVerifyTool(Tool):
    """Run automated verification checks (pytest, ruff, file_exists) on a task."""

    def __init__(
        self,
        manager: TaskLifecycleManager,
        verifier: Verifier,
        diagnostics: DiagnosticsEngine,
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
