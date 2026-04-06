"""IDEOpenTool — ask the connected IDE to open a file."""
from __future__ import annotations

import asyncio

from llm_code.ide.bridge import IDEBridge
from llm_code.tools.base import PermissionLevel, Tool, ToolResult


class IDEOpenTool(Tool):
    def __init__(self, bridge: IDEBridge) -> None:
        self._bridge = bridge

    @property
    def name(self) -> str:
        return "ide_open"

    @property
    def description(self) -> str:
        return "Open a file in the connected IDE at an optional line number."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the file"},
                "line": {
                    "type": "integer",
                    "description": "Line number to jump to (optional)",
                },
            },
            "required": ["path"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    def is_read_only(self, args: dict) -> bool:
        return True

    def is_concurrency_safe(self, args: dict) -> bool:
        return True

    def execute(self, args: dict) -> ToolResult:
        if not self._bridge.is_connected:
            return ToolResult(output="No IDE connected. Use /ide connect first.", is_error=True)

        path = args["path"]
        line = args.get("line")
        loop = asyncio.new_event_loop()
        try:
            ok = loop.run_until_complete(self._bridge.open_file(path, line=line))
        finally:
            loop.close()

        if ok:
            line_str = f" at line {line}" if line else ""
            return ToolResult(output=f"Opened {path}{line_str} in IDE.")
        return ToolResult(output=f"Failed to open {path} in IDE.", is_error=True)
