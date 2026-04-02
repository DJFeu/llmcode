"""WriteFileTool — writes content to a file, auto-creating parent directories."""
from __future__ import annotations

import pathlib

from pydantic import BaseModel

from llm_code.tools.base import PermissionLevel, Tool, ToolResult


class WriteFileInput(BaseModel):
    path: str
    content: str


class WriteFileTool(Tool):
    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Write content to a file, creating parent directories as needed."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to write to"},
                "content": {"type": "string", "description": "Content to write"},
            },
            "required": ["path", "content"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.WORKSPACE_WRITE

    @property
    def input_model(self) -> type[WriteFileInput]:
        return WriteFileInput

    def execute(self, args: dict) -> ToolResult:
        path = pathlib.Path(args["path"])
        content: str = args["content"]

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

        line_count = len(content.splitlines())
        return ToolResult(output=f"Wrote {line_count} lines to {path}")
