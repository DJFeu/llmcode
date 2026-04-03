"""CronDeleteTool — delete a scheduled cron task."""
from __future__ import annotations

from pydantic import BaseModel

from llm_code.cron.storage import CronStorage
from llm_code.tools.base import PermissionLevel, Tool, ToolResult


class CronDeleteInput(BaseModel):
    task_id: str


class CronDeleteTool(Tool):
    def __init__(self, storage: CronStorage) -> None:
        self._storage = storage

    @property
    def name(self) -> str:
        return "cron_delete"

    @property
    def description(self) -> str:
        return "Delete a scheduled cron task by ID."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "ID of the task to delete"},
            },
            "required": ["task_id"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.FULL_ACCESS

    @property
    def input_model(self) -> type[CronDeleteInput]:
        return CronDeleteInput

    def execute(self, args: dict) -> ToolResult:
        task_id = args["task_id"]
        removed = self._storage.remove(task_id)
        if not removed:
            return ToolResult(output=f"Task '{task_id}' not found", is_error=True)
        return ToolResult(output=f"Deleted task {task_id}")
