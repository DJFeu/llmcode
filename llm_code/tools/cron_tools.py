"""Consolidated cron scheduling tools — create, delete, list.

All cron tool classes live here. The original per-file modules
(cron_create, cron_delete, cron_list) re-export from this file
for backward compatibility.
"""
from __future__ import annotations

from pydantic import BaseModel

from llm_code.cron.parser import parse_cron
from llm_code.cron.storage import CronStorage
from llm_code.tools.base import PermissionLevel, Tool, ToolResult


# ---------------------------------------------------------------------------
# CronCreate
# ---------------------------------------------------------------------------

class CronCreateInput(BaseModel):
    cron: str
    prompt: str
    recurring: bool = True
    permanent: bool = False


class CronCreateTool(Tool):
    def __init__(self, storage: CronStorage) -> None:
        self._storage = storage

    @property
    def name(self) -> str:
        return "cron_create"

    @property
    def description(self) -> str:
        return (
            "Schedule a prompt to run on a cron schedule. "
            "5-field format: minute hour day-of-month month day-of-week (local time). "
            "recurring=True keeps firing; permanent=True prevents 30-day auto-expiry."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "cron": {"type": "string", "description": "Cron expression (5-field)"},
                "prompt": {"type": "string", "description": "Prompt to execute when fired"},
                "recurring": {"type": "boolean", "description": "Keep firing (default true)", "default": True},
                "permanent": {"type": "boolean", "description": "Prevent 30-day auto-expiry (default false)", "default": False},
            },
            "required": ["cron", "prompt"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.WORKSPACE_WRITE

    @property
    def input_model(self) -> type[CronCreateInput]:
        return CronCreateInput

    def execute(self, args: dict) -> ToolResult:
        cron_expr = args["cron"]
        prompt = args["prompt"]
        recurring = args.get("recurring", True)
        permanent = args.get("permanent", False)

        # Validate cron expression
        try:
            parse_cron(cron_expr)
        except ValueError as exc:
            return ToolResult(output=f"Invalid cron expression: {exc}", is_error=True)

        try:
            task = self._storage.add(
                cron=cron_expr,
                prompt=prompt,
                recurring=recurring,
                permanent=permanent,
            )
        except ValueError as exc:
            return ToolResult(output=str(exc), is_error=True)

        flags = []
        if recurring:
            flags.append("recurring")
        if permanent:
            flags.append("permanent")
        flag_str = f" ({', '.join(flags)})" if flags else ""

        return ToolResult(
            output=(
                f"Scheduled task {task.id}{flag_str}\n"
                f"  Cron: {task.cron}\n"
                f"  Prompt: {task.prompt}"
            )
        )


# ---------------------------------------------------------------------------
# CronDelete
# ---------------------------------------------------------------------------

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
        return PermissionLevel.WORKSPACE_WRITE

    @property
    def input_model(self) -> type[CronDeleteInput]:
        return CronDeleteInput

    def execute(self, args: dict) -> ToolResult:
        task_id = args["task_id"]
        removed = self._storage.remove(task_id)
        if not removed:
            return ToolResult(output=f"Task '{task_id}' not found", is_error=True)
        return ToolResult(output=f"Deleted task {task_id}")


# ---------------------------------------------------------------------------
# CronList
# ---------------------------------------------------------------------------

class CronListTool(Tool):
    def __init__(self, storage: CronStorage) -> None:
        self._storage = storage

    @property
    def name(self) -> str:
        return "cron_list"

    @property
    def description(self) -> str:
        return "List all scheduled cron tasks with their status."

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    def is_read_only(self, args: dict) -> bool:
        return True

    def is_concurrency_safe(self, args: dict) -> bool:
        return True

    def execute(self, args: dict) -> ToolResult:
        tasks = self._storage.list_all()
        if not tasks:
            return ToolResult(output="No scheduled tasks.")

        lines: list[str] = [f"Scheduled tasks ({len(tasks)}):"]
        for t in tasks:
            flags = []
            if t.recurring:
                flags.append("recurring")
            if t.permanent:
                flags.append("permanent")
            flag_str = f" [{', '.join(flags)}]" if flags else ""
            fired = f", last fired: {t.last_fired_at:%Y-%m-%d %H:%M}" if t.last_fired_at else ""
            lines.append(
                f"  {t.id}  {t.cron}  \"{t.prompt}\"{flag_str}{fired}"
            )
        return ToolResult(output="\n".join(lines))
