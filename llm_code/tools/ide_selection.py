"""IDESelectionTool — get the current editor selection from the connected IDE."""
from __future__ import annotations

import asyncio

from llm_code.hayhooks.ide_rpc import IDEBridge
from llm_code.tools.base import PermissionLevel, Tool, ToolResult


class IDESelectionTool(Tool):
    def __init__(self, bridge: IDEBridge) -> None:
        self._bridge = bridge

    @property
    def name(self) -> str:
        return "ide_selection"

    @property
    def description(self) -> str:
        return "Get the currently selected text in the connected IDE's editor."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {},
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    def is_read_only(self, args: dict) -> bool:
        return True

    def is_concurrency_safe(self, args: dict) -> bool:
        return True

    def execute(self, args: dict) -> ToolResult:
        loop = asyncio.new_event_loop()
        try:
            sel = loop.run_until_complete(self._bridge.get_selection())
        finally:
            loop.close()

        if sel is None:
            return ToolResult(output="No selection — no IDE connected or nothing selected.")

        path = sel.get("path", "unknown")
        start = sel.get("start_line", "?")
        end = sel.get("end_line", "?")
        text = sel.get("text", "")

        header = f"Selection in {path} (lines {start}-{end}):"
        return ToolResult(output=f"{header}\n{text}")
