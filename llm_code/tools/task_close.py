"""TaskCloseTool: finalize a task, write summary, transition to DONE."""
from __future__ import annotations

from pydantic import BaseModel

from llm_code.task.manager import TaskLifecycleManager
from llm_code.task.types import TaskStatus
from llm_code.tools.base import PermissionLevel, Tool, ToolResult


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
