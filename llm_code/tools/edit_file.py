"""EditFileTool — search-and-replace within an existing file."""
from __future__ import annotations

import pathlib

from pydantic import BaseModel

from llm_code.tools.base import PermissionLevel, Tool, ToolResult


class EditFileInput(BaseModel):
    path: str
    old: str
    new: str
    replace_all: bool = False


class EditFileTool(Tool):
    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return "Search and replace text within a file."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the file"},
                "old": {"type": "string", "description": "Text to search for"},
                "new": {"type": "string", "description": "Replacement text"},
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace all occurrences (default false)",
                    "default": False,
                },
            },
            "required": ["path", "old", "new"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.WORKSPACE_WRITE

    @property
    def input_model(self) -> type[EditFileInput]:
        return EditFileInput

    def execute(self, args: dict) -> ToolResult:
        path = pathlib.Path(args["path"])
        old: str = args["old"]
        new: str = args["new"]
        replace_all: bool = bool(args.get("replace_all", False))

        if not path.exists():
            return ToolResult(output=f"File not found: {path}", is_error=True)

        content = path.read_text()
        count = content.count(old)

        if count == 0:
            return ToolResult(
                output=f"Text not found in {path}: {old!r}",
                is_error=True,
            )

        if replace_all:
            new_content = content.replace(old, new)
            replaced = count
        else:
            new_content = content.replace(old, new, 1)
            replaced = 1

        path.write_text(new_content)
        # Build diff-style output for display
        old_lines = old.splitlines()
        new_lines = new.splitlines()
        diff_parts = [f"Replaced {replaced} occurrence(s) in {path}"]
        for line in old_lines[:5]:
            diff_parts.append(f"- {line}")
        for line in new_lines[:5]:
            diff_parts.append(f"+ {line}")
        return ToolResult(output="\n".join(diff_parts))
