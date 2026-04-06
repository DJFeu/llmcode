"""NotebookReadTool — reads Jupyter notebook cells with outputs."""
from __future__ import annotations

import json

from pydantic import BaseModel

from llm_code.tools.base import PermissionLevel, Tool, ToolResult, resolve_path
from llm_code.utils.notebook import format_cells, parse_notebook, validate_notebook


class NotebookReadInput(BaseModel):
    path: str


class NotebookReadTool(Tool):
    @property
    def name(self) -> str:
        return "notebook_read"

    @property
    def description(self) -> str:
        return (
            "Read a Jupyter notebook (.ipynb) file. "
            "Returns all cells with their source code, outputs, and execution counts. "
            "Images from outputs are returned as base64 in metadata."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the .ipynb file"},
            },
            "required": ["path"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    @property
    def input_model(self) -> type[NotebookReadInput]:
        return NotebookReadInput

    def is_read_only(self, args: dict) -> bool:
        return True

    def is_concurrency_safe(self, args: dict) -> bool:
        return True

    def execute(self, args: dict) -> ToolResult:
        path = resolve_path(args["path"])

        if not path.exists():
            return ToolResult(output=f"Notebook not found: {path}", is_error=True)

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

        # Collect all images from all cells
        all_images: list[dict] = []
        for cell in cells:
            all_images.extend(cell.images)

        metadata: dict | None = {"images": all_images} if all_images else None

        return ToolResult(output=output_text, metadata=metadata)
