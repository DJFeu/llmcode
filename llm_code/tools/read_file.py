"""ReadFileTool — reads text files with line numbers, or images as base64."""
from __future__ import annotations

import base64
import pathlib

from llm_code.tools.base import PermissionLevel, Tool, ToolResult

_IMAGE_EXTENSIONS = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
}


class ReadFileTool(Tool):
    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read a file from the filesystem. "
            "Text files are returned with line numbers. "
            "Images are returned as base64 in metadata."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the file"},
                "offset": {
                    "type": "integer",
                    "description": "1-based line number to start reading from (default 1)",
                    "default": 1,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to read (default 2000)",
                    "default": 2000,
                },
            },
            "required": ["path"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    def execute(self, args: dict) -> ToolResult:
        path = pathlib.Path(args["path"])
        offset: int = int(args.get("offset", 1))
        limit: int = int(args.get("limit", 2000))

        if not path.exists():
            return ToolResult(output=f"File not found: {path}", is_error=True)

        suffix = path.suffix.lower()
        if suffix in _IMAGE_EXTENSIONS:
            return self._read_image(path, _IMAGE_EXTENSIONS[suffix])

        return self._read_text(path, offset, limit)

    def _read_image(self, path: pathlib.Path, media_type: str) -> ToolResult:
        data = base64.b64encode(path.read_bytes()).decode()
        return ToolResult(
            output=f"[image: {path.name}]",
            metadata={"type": "image", "media_type": media_type, "data": data},
        )

    def _read_text(self, path: pathlib.Path, offset: int, limit: int) -> ToolResult:
        lines = path.read_text(errors="replace").splitlines()
        # offset is 1-based
        start = max(offset - 1, 0)
        selected = lines[start : start + limit]
        numbered = "\n".join(f"{start + i + 1}\t{line}" for i, line in enumerate(selected))
        return ToolResult(output=numbered)
