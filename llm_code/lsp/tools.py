"""LSP tools: goto-definition, find-references, diagnostics, hover, symbols."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from llm_code.lsp.client import Hover  # noqa: F401  (re-exported via tool tests)
from llm_code.lsp.languages import language_for_file as _language_for_file
from llm_code.lsp.manager import LspServerManager
from llm_code.tools.base import PermissionLevel, Tool, ToolResult


_WORKSPACE_SYMBOL_MAX_RESULTS = 200


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


class LspHoverTool(Tool):
    """Get hover information (type signature, docs) at a file position."""

    def __init__(self, manager: LspServerManager) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "lsp_hover"

    @property
    def description(self) -> str:
        return (
            "Show hover information (type signature, doc comment) for the symbol "
            "at the given file position via the language server."
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
        hover = await client.hover(file_uri, line, column)
        if not hover.contents:
            return ToolResult(output="No hover information at that position.")
        return ToolResult(output=hover.contents)


class LspDocumentSymbolTool(Tool):
    """List all symbols (classes, functions, variables) declared in a file."""

    def __init__(self, manager: LspServerManager) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "lsp_document_symbol"

    @property
    def description(self) -> str:
        return (
            "List all top-level and nested symbols (classes, functions, variables) "
            "declared in a file via the language server."
        )

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
        symbols = await client.document_symbol(file_uri)
        if not symbols:
            return ToolResult(output="No symbols found.")
        lines = [f"{s.kind} {s.name}\t{s.line}:{s.column}" for s in symbols]
        return ToolResult(output="\n".join(lines))


class LspImplementationTool(Tool):
    """Find the concrete implementation(s) of a method, interface, or abstract symbol."""

    def __init__(self, manager: LspServerManager) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "lsp_implementation"

    @property
    def description(self) -> str:
        return (
            "Jump from an interface, abstract method, or trait declaration to its "
            "concrete implementations via the language server. Useful for "
            "answering 'who implements this?'."
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
        locations = await client.go_to_implementation(file_uri, line, column)
        if not locations:
            return ToolResult(output="No implementation found.")
        return ToolResult(output="\n".join(f"{loc.file}:{loc.line}:{loc.column}" for loc in locations))


class _QueryInput(BaseModel):
    query: str


class LspWorkspaceSymbolTool(Tool):
    """Search for a symbol across the entire workspace via the language server."""

    def __init__(self, manager: LspServerManager) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "lsp_workspace_symbol"

    @property
    def description(self) -> str:
        return (
            "Fuzzy-search for a symbol (class, function, variable) across the "
            "entire workspace using the language server's workspace/symbol "
            "request. Faster and more precise than grep for code identifiers."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Symbol query string (fuzzy match)",
                },
            },
            "required": ["query"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    @property
    def input_model(self) -> type[_QueryInput]:
        return _QueryInput

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
        query = str(args.get("query", "")).strip()
        if not query:
            return ToolResult(
                output="workspace_symbol requires a non-empty query",
                is_error=True,
            )
        client = self._manager.any_client()
        if client is None:
            return ToolResult(
                output="No LSP server is currently running. Start a project with a known marker file.",
                is_error=True,
            )
        symbols = await client.workspace_symbol(query)
        if not symbols:
            return ToolResult(output=f"No symbols matching '{query}'.")
        total = len(symbols)
        if total > _WORKSPACE_SYMBOL_MAX_RESULTS:
            shown = symbols[:_WORKSPACE_SYMBOL_MAX_RESULTS]
            tail = f"\n(+{total - _WORKSPACE_SYMBOL_MAX_RESULTS} more)"
        else:
            shown = symbols
            tail = ""
        lines = [f"{s.kind} {s.name}\t{s.file}:{s.line}:{s.column}" for s in shown]
        return ToolResult(output="\n".join(lines) + tail)


class _CallHierarchyInput(BaseModel):
    file: str
    line: int
    column: int
    direction: Literal["incoming", "outgoing", "both"] = "both"


_VALID_DIRECTIONS: frozenset[str] = frozenset({"incoming", "outgoing", "both"})


class LspCallHierarchyTool(Tool):
    """Show callers and/or callees of the symbol at a file position."""

    def __init__(self, manager: LspServerManager) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "lsp_call_hierarchy"

    @property
    def description(self) -> str:
        return (
            "Show the call hierarchy of the symbol at the given file position. "
            "direction='incoming' lists callers (who calls this?), "
            "direction='outgoing' lists callees (what does this call?), "
            "direction='both' (default) lists both."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "Absolute path to the file"},
                "line": {"type": "integer", "description": "0-based line number"},
                "column": {"type": "integer", "description": "0-based column number"},
                "direction": {
                    "type": "string",
                    "enum": ["incoming", "outgoing", "both"],
                    "description": "Which direction(s) of the call hierarchy to fetch.",
                },
            },
            "required": ["file", "line", "column"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    @property
    def input_model(self) -> type[_CallHierarchyInput]:
        return _CallHierarchyInput

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
        direction = str(args.get("direction", "both")).lower()

        if direction not in _VALID_DIRECTIONS:
            return ToolResult(
                output=f"Invalid direction '{direction}'. Use 'incoming', 'outgoing', or 'both'.",
                is_error=True,
            )

        language = _language_for_file(file_path)
        client = self._manager.get_client(language)
        if client is None:
            return ToolResult(
                output=f"No LSP client available for language '{language}' (file: {file_path})",
                is_error=True,
            )

        file_uri = Path(file_path).as_uri()
        items = await client.prepare_call_hierarchy(file_uri, line, column)
        if not items:
            return ToolResult(output="No symbol at that position (call hierarchy could not be prepared).")

        target = items[0]
        sections: list[str] = [f"Symbol: {target.kind} {target.name} @ {target.file}:{target.line}:{target.column}"]

        import asyncio as _asyncio

        want_in = direction in ("incoming", "both")
        want_out = direction in ("outgoing", "both")

        async def _noop() -> list:
            return []

        # Run both directions concurrently when both are requested.
        callers, callees = await _asyncio.gather(
            client.incoming_calls(target) if want_in else _noop(),
            client.outgoing_calls(target) if want_out else _noop(),
        )

        if want_in:
            if callers:
                sections.append("Incoming (callers):")
                sections.extend(f"  {c.kind} {c.name}\t{c.file}:{c.line}:{c.column}" for c in callers)
            else:
                sections.append("Incoming (callers): (none)")

        if want_out:
            if callees:
                sections.append("Outgoing (callees):")
                sections.extend(f"  {c.kind} {c.name}\t{c.file}:{c.line}:{c.column}" for c in callees)
            else:
                sections.append("Outgoing (callees): (none)")

        return ToolResult(output="\n".join(sections))
