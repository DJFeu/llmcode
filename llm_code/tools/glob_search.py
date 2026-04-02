"""GlobSearchTool — find files matching a glob pattern, sorted by mtime."""
from __future__ import annotations

import pathlib

from llm_code.tools.base import PermissionLevel, Tool, ToolResult

_MAX_RESULTS = 100


class GlobSearchTool(Tool):
    @property
    def name(self) -> str:
        return "glob_search"

    @property
    def description(self) -> str:
        return (
            "Search for files matching a glob pattern. "
            "Returns up to 100 results sorted by modification time (newest first)."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern (e.g. **/*.py)"},
                "path": {
                    "type": "string",
                    "description": "Directory to search in (default: current dir)",
                },
            },
            "required": ["pattern"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    def execute(self, args: dict) -> ToolResult:
        pattern: str = args["pattern"]
        search_path = pathlib.Path(args.get("path", "."))

        try:
            matches = list(search_path.glob(pattern))
        except Exception as exc:
            return ToolResult(output=f"Glob error: {exc}", is_error=True)

        # Sort by mtime descending (newest first)
        matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        matches = matches[:_MAX_RESULTS]

        if not matches:
            return ToolResult(output=f"No files matched: {pattern}")

        return ToolResult(output="\n".join(str(m) for m in matches))
