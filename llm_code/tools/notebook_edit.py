"""NotebookEditTool — replace, insert, or delete cells in a Jupyter notebook."""
from __future__ import annotations

import json

from pydantic import BaseModel

from llm_code.tools.base import PermissionLevel, Tool, ToolResult, resolve_path
from llm_code.utils.notebook import edit_notebook, validate_notebook


class NotebookEditInput(BaseModel):
    path: str
    command: str
    cell_index: int
    source: str | None = None
    cell_type: str | None = None


class NotebookEditTool(Tool):
    @property
    def name(self) -> str:
        return "notebook_edit"

    @property
    def description(self) -> str:
        return (
            "Edit a Jupyter notebook (.ipynb) file by replacing, inserting, or deleting a cell. "
            "Use 'replace' to change a cell's source, 'insert' to add a new cell before a given index, "
            "or 'delete' to remove a cell."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the .ipynb file"},
                "command": {
                    "type": "string",
                    "enum": ["replace", "insert", "delete"],
                    "description": "Edit command: replace, insert, or delete",
                },
                "cell_index": {
                    "type": "integer",
                    "description": "0-based cell index to operate on",
                },
                "source": {
                    "type": "string",
                    "description": "New cell source (required for replace and insert)",
                },
                "cell_type": {
                    "type": "string",
                    "enum": ["code", "markdown", "raw"],
                    "description": "Cell type for insert or replace (default: code)",
                },
            },
            "required": ["path", "command", "cell_index"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.WORKSPACE_WRITE

    @property
    def input_model(self) -> type[NotebookEditInput]:
        return NotebookEditInput

    def execute(self, args: dict) -> ToolResult:
        path = resolve_path(args["path"])
        command: str = args["command"]
        cell_index: int = int(args["cell_index"])
        source: str | None = args.get("source")
        cell_type: str | None = args.get("cell_type")

        if command not in ("replace", "insert", "delete"):
            return ToolResult(
                output=f"Invalid command {command!r}. Use replace, insert, or delete.",
                is_error=True,
            )

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

        try:
            updated = edit_notebook(data, command, cell_index, source=source, cell_type=cell_type)
        except (IndexError, ValueError) as exc:
            return ToolResult(output=str(exc), is_error=True)

        path.write_text(json.dumps(updated, indent=1, ensure_ascii=False), encoding="utf-8")

        n_cells = len(updated.get("cells", []))
        return ToolResult(
            output=f"Notebook updated: {command} at cell {cell_index}. Notebook now has {n_cells} cell(s)."
        )
