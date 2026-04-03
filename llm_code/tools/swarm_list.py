"""SwarmListTool — list all active swarm members."""
from __future__ import annotations

from llm_code.swarm.manager import SwarmManager
from llm_code.tools.base import PermissionLevel, Tool, ToolResult


class SwarmListTool(Tool):
    def __init__(self, manager: SwarmManager) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "swarm_list"

    @property
    def description(self) -> str:
        return "List all active swarm worker agents with their roles, tasks, and status."

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
        members = self._manager.list_members()
        if not members:
            return ToolResult(output="No swarm members active.")
        lines = []
        for m in members:
            lines.append(
                f"- {m.id} | role={m.role} | task={m.task[:50]} | "
                f"backend={m.backend} | pid={m.pid} | status={m.status.value}"
            )
        return ToolResult(output="\n".join(lines))
