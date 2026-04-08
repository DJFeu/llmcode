"""LSP tools: goto-definition, find-references, diagnostics, hover, symbols."""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from llm_code.lsp.languages import language_for_file as _language_for_file
from llm_code.lsp.manager import LspServerManager
from llm_code.tools.base import PermissionLevel, Tool, ToolResult


# ---------------------------------------------------------------------------
# Pydantic input models
# ---------------------------------------------------------------------------


class _PositionInput(BaseModel):
    file: str
    line: int
    column: int


class _FileInput(BaseModel):
    file: str


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


class LspGotoDefinitionTool(Tool):
    """Jump to the definition of a symbol at a given file position."""

    def __init__(self, manager: LspServerManager) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "lsp_goto_definition"

    @property
    def description(self) -> str:
        return (
            "Go to the definition of the symbol at the given file position "
            "using the language server."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "Absolute path to the file"},
                "line": {
                    "type": "integer",
                    "description": "0-based line number",
                },
                "column": {
                    "type": "integer",
                    "description": "0-based column number",
                },
            },
            "required": ["file", "line", "column"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    @property
    def input_model(self) -> type[_PositionInput]:
        return _PositionInput

    def is_read_only(self, args: dict) -> bool:
        return True

    def is_concurrency_safe(self, args: dict) -> bool:
        return True

    def execute(self, args: dict) -> ToolResult:
        import asyncio
        import concurrent.futures
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, self.execute_async(args)).result()
        return asyncio.run(self.execute_async(args))

    async def execute_async(self, args: dict) -> ToolResult:
        file_path = args["file"]
        line = int(args["line"])
        column = int(args["column"])

        language = _language_for_file(file_path)
        client = self._manager.get_client(language)
        if client is None:
            return ToolResult(
                output=f"No LSP client available for language '{language}' (file: {file_path})",
                is_error=True,
            )

        file_uri = Path(file_path).as_uri()
        locations = await client.goto_definition(file_uri, line, column)

        if not locations:
            return ToolResult(output="No definition found.")

        lines = []
        for loc in locations:
            lines.append(f"{loc.file}:{loc.line}:{loc.column}")
        return ToolResult(output="\n".join(lines))


class LspFindReferencesTool(Tool):
    """Find all references to the symbol at a given file position."""

    def __init__(self, manager: LspServerManager) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "lsp_find_references"

    @property
    def description(self) -> str:
        return (
            "Find all references to the symbol at the given file position "
            "using the language server."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "Absolute path to the file"},
                "line": {"type": "integer", "description": "0-based line number"},
                "column": {"type": "integer", "description": "0-based column number"},
            },
            "required": ["file", "line", "column"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    @property
    def input_model(self) -> type[_PositionInput]:
        return _PositionInput

    def is_read_only(self, args: dict) -> bool:
        return True

    def is_concurrency_safe(self, args: dict) -> bool:
        return True

    def execute(self, args: dict) -> ToolResult:
        import asyncio
        import concurrent.futures
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, self.execute_async(args)).result()
        return asyncio.run(self.execute_async(args))

    async def execute_async(self, args: dict) -> ToolResult:
        file_path = args["file"]
        line = int(args["line"])
        column = int(args["column"])

        language = _language_for_file(file_path)
        client = self._manager.get_client(language)
        if client is None:
            return ToolResult(
                output=f"No LSP client available for language '{language}' (file: {file_path})",
                is_error=True,
            )

        file_uri = Path(file_path).as_uri()
        references = await client.find_references(file_uri, line, column)

        if not references:
            return ToolResult(output="No references found.")

        lines = []
        for loc in references:
            lines.append(f"{loc.file}:{loc.line}:{loc.column}")
        return ToolResult(output="\n".join(lines))


class LspDiagnosticsTool(Tool):
    """Get diagnostics (errors, warnings) for a file from the language server."""

    def __init__(self, manager: LspServerManager) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "lsp_diagnostics"

    @property
    def description(self) -> str:
        return "Get diagnostics (errors, warnings, hints) for a file using the language server."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "Absolute path to the file"},
            },
            "required": ["file"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    @property
    def input_model(self) -> type[_FileInput]:
        return _FileInput

    def is_read_only(self, args: dict) -> bool:
        return True

    def is_concurrency_safe(self, args: dict) -> bool:
        return True

    def execute(self, args: dict) -> ToolResult:
        import asyncio
        import concurrent.futures
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, self.execute_async(args)).result()
        return asyncio.run(self.execute_async(args))

    async def execute_async(self, args: dict) -> ToolResult:
        file_path = args["file"]

        language = _language_for_file(file_path)
        client = self._manager.get_client(language)
        if client is None:
            return ToolResult(
                output=f"No LSP client available for language '{language}' (file: {file_path})",
                is_error=True,
            )

        file_uri = Path(file_path).as_uri()
        diagnostics = await client.get_diagnostics(file_uri)

        if not diagnostics:
            return ToolResult(output="No diagnostics found — file looks clean.")

        lines = []
        for d in diagnostics:
            lines.append(f"{d.file}:{d.line}:{d.column} [{d.severity}] {d.message} ({d.source})")
        return ToolResult(output="\n".join(lines))
