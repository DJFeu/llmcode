"""GrepSearchTool — regex search across files."""
from __future__ import annotations

import re
from typing import Callable

from pydantic import BaseModel

from llm_code.tools.base import PermissionLevel, Tool, ToolProgress, ToolResult, resolve_path

_MAX_MATCHES = 100
_MAX_FILES_SCANNED = 500
_PROGRESS_INTERVAL = 100  # emit a progress event every N files scanned


class GrepSearchInput(BaseModel):
    pattern: str
    path: str = "."
    glob: str = "**/*"
    context: int = 0


class GrepSearchTool(Tool):
    @property
    def name(self) -> str:
        return "grep_search"

    @property
    def description(self) -> str:
        return (
            "Search for a regex pattern across files in a directory. "
            "Returns up to 100 matches across up to 500 files."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for"},
                "path": {
                    "type": "string",
                    "description": "Directory to search in (default: current dir)",
                },
                "glob": {
                    "type": "string",
                    "description": "Glob filter for filenames (e.g. *.py)",
                },
                "context": {
                    "type": "integer",
                    "description": "Lines of context to include before and after each match",
                    "default": 0,
                },
            },
            "required": ["pattern"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    @property
    def input_model(self) -> type[GrepSearchInput]:
        return GrepSearchInput

    def is_read_only(self, args: dict) -> bool:
        return True

    def is_concurrency_safe(self, args: dict) -> bool:
        return True

    def execute(self, args: dict) -> ToolResult:
        return self._search(args, on_progress=None)

    def execute_with_progress(
        self,
        args: dict,
        on_progress: Callable[[ToolProgress], None],
    ) -> ToolResult:
        return self._search(args, on_progress=on_progress)

    def _search(
        self,
        args: dict,
        on_progress: Callable[[ToolProgress], None] | None,
    ) -> ToolResult:
        pattern_str: str = args["pattern"]
        search_path = resolve_path(args.get("path", "."))
        glob_filter: str = args.get("glob", "**/*")
        context_lines: int = int(args.get("context", 0))

        try:
            regex = re.compile(pattern_str)
        except re.error as exc:
            return ToolResult(output=f"Invalid regex: {exc}", is_error=True)

        # Collect candidate files
        try:
            candidates = [p for p in search_path.glob(glob_filter) if p.is_file()]
        except Exception as exc:
            return ToolResult(output=f"Glob error: {exc}", is_error=True)

        candidates = candidates[:_MAX_FILES_SCANNED]
        total = len(candidates)

        results: list[str] = []
        match_count = 0

        for file_idx, file_path in enumerate(candidates, start=1):
            if match_count >= _MAX_MATCHES:
                break

            # Emit progress every PROGRESS_INTERVAL files
            if on_progress is not None and file_idx % _PROGRESS_INTERVAL == 0:
                percent = round(file_idx / total * 100.0, 1) if total else 100.0
                on_progress(
                    ToolProgress(
                        tool_name=self.name,
                        message=f"Scanned {file_idx}/{total} files",
                        percent=percent,
                    )
                )

            try:
                lines = file_path.read_text(errors="replace").splitlines()
            except Exception:
                continue

            for i, line in enumerate(lines):
                if match_count >= _MAX_MATCHES:
                    break
                if regex.search(line):
                    # Gather context
                    start = max(0, i - context_lines)
                    end = min(len(lines), i + context_lines + 1)
                    block = [f"{file_path}:{start + j + 1}: {lines[start + j]}" for j in range(end - start)]
                    results.append("\n".join(block))
                    match_count += 1

        if not results:
            return ToolResult(output=f"No matches found for: {pattern_str}")

        return ToolResult(output="\n---\n".join(results))
