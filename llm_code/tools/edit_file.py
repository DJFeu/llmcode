"""EditFileTool — search-and-replace within an existing file."""
from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

from pydantic import BaseModel

from llm_code.runtime.file_protection import check_write
from llm_code.tools.base import PermissionLevel, Tool, ToolResult

if TYPE_CHECKING:
    from llm_code.runtime.overlay import OverlayFS


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

    def execute(self, args: dict, overlay: "OverlayFS | None" = None) -> ToolResult:
        path = pathlib.Path(args["path"])
        old: str = args["old"]
        new: str = args["new"]
        replace_all: bool = bool(args.get("replace_all", False))

        protection = check_write(str(path))
        if not protection.allowed:
            return ToolResult(output=protection.reason, is_error=True)
        warning_prefix = f"[WARNING] {protection.reason}\n" if protection.severity == "warn" else ""

        # Resolve content source: overlay first, then real FS
        if overlay is not None:
            try:
                content = overlay.read(path)
            except FileNotFoundError:
                return ToolResult(output=f"File not found: {path}", is_error=True)
        else:
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

        if overlay is not None:
            overlay.write(path, new_content)
        else:
            path.write_text(new_content)

        # Generate structured diff
        from llm_code.utils.diff import generate_diff, count_changes

        hunks = generate_diff(content, new_content, path.name)
        adds, dels = count_changes(hunks)

        diff_parts = [warning_prefix + f"Replaced {replaced} occurrence(s) in {path}"]
        for line in old.splitlines()[:5]:
            diff_parts.append(f"- {line}")
        for line in new.splitlines()[:5]:
            diff_parts.append(f"+ {line}")

        return ToolResult(
            output="\n".join(diff_parts),
            metadata={
                "diff": [h.to_dict() for h in hunks],
                "additions": adds,
                "deletions": dels,
            },
        )
