"""CronCreateTool — schedule a new cron task."""
from __future__ import annotations

from pydantic import BaseModel

from llm_code.cron.parser import parse_cron
from llm_code.cron.storage import CronStorage
from llm_code.tools.base import PermissionLevel, Tool, ToolResult


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
        return PermissionLevel.FULL_ACCESS

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
