"""GlobSearchTool — find files matching a glob pattern, sorted by mtime."""
from __future__ import annotations

from typing import Callable

from pydantic import BaseModel

from llm_code.tools.base import PermissionLevel, Tool, ToolProgress, ToolResult, resolve_path

_MAX_RESULTS = 100
_PROGRESS_INTERVAL = 50  # emit a progress event every N files scanned


class GlobSearchInput(BaseModel):
    pattern: str
    path: str = "."


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

    @property
    def input_model(self) -> type[GlobSearchInput]:
        return GlobSearchInput

    def is_read_only(self, args: dict) -> bool:
        return True

    def is_concurrency_safe(self, args: dict) -> bool:
        return True

    def execute(self, args: dict) -> ToolResult:
        pattern: str = args["pattern"]
        search_path = resolve_path(args.get("path", "."))

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

    def execute_with_progress(
        self,
        args: dict,
        on_progress: Callable[[ToolProgress], None],
    ) -> ToolResult:
        pattern: str = args["pattern"]
        search_path = resolve_path(args.get("path", "."))

        try:
            all_matches = list(search_path.glob(pattern))
        except Exception as exc:
            return ToolResult(output=f"Glob error: {exc}", is_error=True)

        total = len(all_matches)

        # Emit progress every PROGRESS_INTERVAL files
        for i, _ in enumerate(all_matches, start=1):
            if i % _PROGRESS_INTERVAL == 0:
                percent = round(i / total * 100.0, 1) if total else 100.0
                on_progress(
                    ToolProgress(
                        tool_name=self.name,
                        message=f"Scanned {i}/{total} files",
                        percent=percent,
                    )
                )

        # Sort by mtime descending (newest first)
        all_matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        matches = all_matches[:_MAX_RESULTS]

        if not matches:
            return ToolResult(output=f"No files matched: {pattern}")

        return ToolResult(output="\n".join(str(m) for m in matches))
