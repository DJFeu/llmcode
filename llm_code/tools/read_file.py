"""ReadFileTool — reads text files with line numbers, or images as base64."""
from __future__ import annotations

import base64
import json
import pathlib

from pydantic import BaseModel

from llm_code.tools.base import PermissionLevel, Tool, ToolResult


class ReadFileInput(BaseModel):
    path: str
    offset: int = 1
    limit: int = 2000

_NOTEBOOK_EXTENSION = ".ipynb"

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

    @property
    def input_model(self) -> type[ReadFileInput]:
        return ReadFileInput

    def is_read_only(self, args: dict) -> bool:
        return True

    def is_concurrency_safe(self, args: dict) -> bool:
        return True

    def execute(self, args: dict) -> ToolResult:
        path = pathlib.Path(args["path"])
        offset: int = int(args.get("offset", 1))
        limit: int = int(args.get("limit", 2000))

        if not path.exists():
            return ToolResult(output=f"File not found: {path}", is_error=True)

        suffix = path.suffix.lower()

        if suffix == _NOTEBOOK_EXTENSION:
            return self._read_notebook(path)

        if suffix in _IMAGE_EXTENSIONS:
            return self._read_image(path, _IMAGE_EXTENSIONS[suffix])

        return self._read_text(path, offset, limit)

    def _read_notebook(self, path: pathlib.Path) -> ToolResult:
        from llm_code.utils.notebook import format_cells, parse_notebook, validate_notebook

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return ToolResult(output=f"Failed to parse notebook JSON: {exc}", is_error=True)

        if not validate_notebook(data):
            return ToolResult(
                output="Invalid notebook: requires nbformat >= 4 and a cells list.",
                is_error=True,
            )

        cells = parse_notebook(data)
        output_text = format_cells(cells)

        all_images: list[dict] = []
        for cell in cells:
            all_images.extend(cell.images)

        metadata: dict | None = {"images": all_images} if all_images else None
        return ToolResult(output=output_text, metadata=metadata)

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
