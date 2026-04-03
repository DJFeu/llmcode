"""IDEDiagnosticsTool — get diagnostics from the connected IDE."""
from __future__ import annotations

import asyncio

from llm_code.ide.bridge import IDEBridge
from llm_code.tools.base import PermissionLevel, Tool, ToolResult


class IDEDiagnosticsTool(Tool):
    def __init__(self, bridge: IDEBridge) -> None:
        self._bridge = bridge

    @property
    def name(self) -> str:
        return "ide_diagnostics"

    @property
    def description(self) -> str:
        return "Get diagnostics (errors, warnings) for a file from the connected IDE."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the file"},
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
        path = args["path"]
        loop = asyncio.get_event_loop()
        diags = loop.run_until_complete(self._bridge.get_diagnostics(path))

        if not diags:
            return ToolResult(output=f"No diagnostics for {path}.")

        lines: list[str] = [f"Diagnostics for {path} ({len(diags)} issues):"]
        for d in diags:
            line_num = d.get("line", "?")
            severity = d.get("severity", "info")
            message = d.get("message", "")
            source = d.get("source", "")
            src_str = f" [{source}]" if source else ""
            lines.append(f"  L{line_num} {severity}: {message}{src_str}")

        return ToolResult(output="\n".join(lines))
