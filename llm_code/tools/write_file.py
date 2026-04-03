"""WriteFileTool — writes content to a file, auto-creating parent directories."""
from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

from pydantic import BaseModel

from llm_code.runtime.file_protection import check_write
from llm_code.tools.base import PermissionLevel, Tool, ToolResult

if TYPE_CHECKING:
    from llm_code.runtime.overlay import OverlayFS


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

    def execute(self, args: dict, overlay: "OverlayFS | None" = None) -> ToolResult:
        path = pathlib.Path(args["path"])
        content: str = args["content"]

        protection = check_write(str(path))
        if not protection.allowed:
            return ToolResult(output=protection.reason, is_error=True)
        if protection.severity == "warn":
            # Surface the warning in output metadata; execution still proceeds
            warning_prefix = f"[WARNING] {protection.reason}\n"
        else:
            warning_prefix = ""

        if overlay is not None:
            # Speculative mode: write to overlay, read old content from overlay/real FS
            old_content: str | None = None
            try:
                old_content = overlay.read(path)
            except FileNotFoundError:
                pass

            overlay.write(path, content)

            line_count = len(content.splitlines())
            output = warning_prefix + f"Wrote {line_count} lines to {path}"

            metadata: dict | None = None
            if old_content is not None and old_content != content:
                from llm_code.utils.diff import generate_diff, count_changes

                hunks = generate_diff(old_content, content, path.name)
                adds, dels = count_changes(hunks)
                metadata = {
                    "diff": [h.to_dict() for h in hunks],
                    "additions": adds,
                    "deletions": dels,
                }

            return ToolResult(output=output, metadata=metadata)

        # Normal mode: write directly to the real filesystem
        # Capture old content if overwriting
        old_content = None
        if path.exists():
            old_content = path.read_text()

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

        line_count = len(content.splitlines())
        output = warning_prefix + f"Wrote {line_count} lines to {path}"

        # Generate diff for overwrites
        metadata = None
        if old_content is not None and old_content != content:
            from llm_code.utils.diff import generate_diff, count_changes

            hunks = generate_diff(old_content, content, path.name)
            adds, dels = count_changes(hunks)
            metadata = {
                "diff": [h.to_dict() for h in hunks],
                "additions": adds,
                "deletions": dels,
            }

        return ToolResult(output=output, metadata=metadata)
