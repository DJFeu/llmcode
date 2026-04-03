"""CronListTool — list all scheduled cron tasks."""
from __future__ import annotations

from llm_code.cron.storage import CronStorage
from llm_code.tools.base import PermissionLevel, Tool, ToolResult


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
