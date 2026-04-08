"""LSP client: types, LspTransport, and LspClient."""
from __future__ import annotations

import asyncio
import itertools
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Location:
    file: str
    line: int
    column: int


@dataclass(frozen=True)
class Diagnostic:
    file: str
    line: int
    column: int
    severity: str  # "error" | "warning" | "info" | "hint"
    message: str
    source: str


@dataclass(frozen=True)
class Hover:
    contents: str


@dataclass(frozen=True)
class SymbolInfo:
    name: str
    kind: str
    file: str
    line: int
    column: int


# LSP SymbolKind enum -> human label
_SYMBOL_KIND: dict[int, str] = {
    1: "file", 2: "module", 3: "namespace", 4: "package", 5: "class",
    6: "method", 7: "property", 8: "field", 9: "constructor", 10: "enum",
    11: "interface", 12: "function", 13: "variable", 14: "constant",
    15: "string", 16: "number", 17: "boolean", 18: "array", 19: "object",
    20: "key", 21: "null", 22: "enum_member", 23: "struct", 24: "event",
    25: "operator", 26: "type_parameter",
}


@dataclass(frozen=True)
class CallHierarchyItem:
    """A symbol participating in a call hierarchy.

    Carries enough information to round-trip back through callHierarchy/* requests.

    The ``raw`` field stores the original LSP response dict so opaque
    server-private fields like ``data`` (used by rust-analyzer, jdtls, etc.)
    plus full ``range``/``selectionRange``/``tags`` are echoed back verbatim
    on subsequent ``callHierarchy/incomingCalls`` and ``outgoingCalls``
    requests. Without this, many servers silently return empty arrays.
    """
    name: str
    kind: str
    file: str
    line: int
    column: int
    raw: dict[str, Any] = field(default_factory=dict)


_SYMBOL_KIND_INV: dict[str, int] = {label: code for code, label in _SYMBOL_KIND.items()}


@dataclass(frozen=True)
class LspServerConfig:
    command: str
    args: tuple[str, ...] = ()
    language: str = ""


# ---------------------------------------------------------------------------
# Transport abstraction
# ---------------------------------------------------------------------------

_SEVERITY_MAP: dict[int, str] = {
    1: "error",
    2: "warning",
    3: "info",
    4: "hint",
}


class LspTransport(ABC):
    """Abstract base for LSP transports (Content-Length framed JSON-RPC)."""

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def send_message(self, message: dict[str, Any]) -> None: ...

    @abstractmethod
    async def receive_message(self) -> dict[str, Any]: ...

    @abstractmethod
    async def close(self) -> None: ...


class StdioLspTransport(LspTransport):
    """LSP transport over subprocess stdin/stdout using Content-Length framing."""

    RECEIVE_TIMEOUT = 30.0
    CLOSE_WAIT_TIMEOUT = 5.0

    def __init__(
        self,
        command: str,
        args: tuple[str, ...] = (),
    ) -> None:
        self._command = command
        self._args = args
        self._process: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        self._process = await asyncio.create_subprocess_exec(
            self._command,
            *self._args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ},
        )

    async def send_message(self, message: dict[str, Any]) -> None:
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("Transport not started")
        body = json.dumps(message).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self._process.stdin.write(header + body)
        await self._process.stdin.drain()

    async def receive_message(self) -> dict[str, Any]:
        if self._process is None or self._process.stdout is None:
            raise RuntimeError("Transport not started")

        headers: dict[str, str] = {}
        while True:
            line = await asyncio.wait_for(
                self._process.stdout.readline(),
                timeout=self.RECEIVE_TIMEOUT,
            )
            line_str = line.decode("ascii").strip()
            if not line_str:
                break
            key, _, value = line_str.partition(":")
            headers[key.strip().lower()] = value.strip()

        content_length = int(headers.get("content-length", "0"))
        body = await asyncio.wait_for(
            self._process.stdout.readexactly(content_length),
            timeout=self.RECEIVE_TIMEOUT,
        )
        return json.loads(body.decode("utf-8"))

    async def close(self) -> None:
        if self._process is None:
            return
        process = self._process
        self._process = None
        try:
            if process.stdin and not process.stdin.is_closing():
                process.stdin.close()
        except Exception:
            pass
        try:
            process.terminate()
        except (ProcessLookupError, OSError):
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=self.CLOSE_WAIT_TIMEOUT)
        except asyncio.TimeoutError:
            try:
                process.kill()
            except (ProcessLookupError, OSError):
                pass
            try:
                await process.wait()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# LSP client
# ---------------------------------------------------------------------------


class LspClient:
    """High-level LSP client."""

    def __init__(self, transport: LspTransport) -> None:
        self._transport = transport
        self._id_counter = itertools.count(1)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def initialize(self, root_uri: str) -> dict[str, Any]:
        result = await self._request(
            "initialize",
            {
                "processId": None,
                "clientInfo": {"name": "llm-code", "version": "1.0.2"},
                "rootUri": root_uri,
                "capabilities": {},
            },
        )
        return result

    async def goto_definition(
        self, file_uri: str, line: int, col: int
    ) -> list[Location]:
        result = await self._request(
            "textDocument/definition",
            {
                "textDocument": {"uri": file_uri},
                "position": {"line": line, "character": col},
            },
        )
        return self._parse_locations(result)

    async def find_references(
        self, file_uri: str, line: int, col: int
    ) -> list[Location]:
        result = await self._request(
            "textDocument/references",
            {
                "textDocument": {"uri": file_uri},
                "position": {"line": line, "character": col},
                "context": {"includeDeclaration": True},
            },
        )
        return self._parse_locations(result)

    async def go_to_implementation(
        self, file_uri: str, line: int, col: int
    ) -> list[Location]:
        result = await self._request(
            "textDocument/implementation",
            {
                "textDocument": {"uri": file_uri},
                "position": {"line": line, "character": col},
            },
        )
        return self._parse_locations(result)

    async def hover(self, file_uri: str, line: int, col: int) -> Hover:
        result = await self._request(
            "textDocument/hover",
            {
                "textDocument": {"uri": file_uri},
                "position": {"line": line, "character": col},
            },
        )
        return Hover(contents=self._parse_hover_contents(result))

    @staticmethod
    def _parse_hover_contents(result: Any) -> str:
        """Normalize the three LSP hover content shapes into a single string."""
        if result is None:
            return ""
        contents = result.get("contents") if isinstance(result, dict) else None
        if contents is None:
            return ""
        if isinstance(contents, str):
            return contents
        if isinstance(contents, dict):
            return contents.get("value", "") or ""
        if isinstance(contents, list):
            parts: list[str] = []
            for item in contents:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    parts.append(item.get("value", "") or "")
            return "\n\n".join(p for p in parts if p)
        return ""

    async def document_symbol(self, file_uri: str) -> list[SymbolInfo]:
        result = await self._request(
            "textDocument/documentSymbol",
            {"textDocument": {"uri": file_uri}},
        )
        return self._parse_symbols(result, default_uri=file_uri)

    def _parse_symbols(self, result: Any, default_uri: str) -> list[SymbolInfo]:
        """Parse both DocumentSymbol[] and SymbolInformation[] shapes."""
        if not result:
            return []
        out: list[SymbolInfo] = []
        for item in result:
            if "location" in item:
                loc = item["location"]
                start = loc.get("range", {}).get("start", {})
                out.append(
                    SymbolInfo(
                        name=item.get("name", ""),
                        kind=_SYMBOL_KIND.get(item.get("kind", 0), "unknown"),
                        file=loc.get("uri", default_uri),
                        line=start.get("line", 0),
                        column=start.get("character", 0),
                    )
                )
            else:
                self._collect_document_symbol(item, default_uri, out)
        return out

    def _collect_document_symbol(
        self, node: dict, default_uri: str, accum: list[SymbolInfo]
    ) -> None:
        sel = node.get("selectionRange") or node.get("range") or {}
        start = sel.get("start", {})
        accum.append(
            SymbolInfo(
                name=node.get("name", ""),
                kind=_SYMBOL_KIND.get(node.get("kind", 0), "unknown"),
                file=default_uri,
                line=start.get("line", 0),
                column=start.get("character", 0),
            )
        )
        for child in node.get("children", []) or []:
            self._collect_document_symbol(child, default_uri, accum)

    async def workspace_symbol(self, query: str) -> list[SymbolInfo]:
        result = await self._request(
            "workspace/symbol",
            {"query": query},
        )
        return self._parse_symbols(result, default_uri="")

    async def prepare_call_hierarchy(
        self, file_uri: str, line: int, col: int
    ) -> list[CallHierarchyItem]:
        result = await self._request(
            "textDocument/prepareCallHierarchy",
            {
                "textDocument": {"uri": file_uri},
                "position": {"line": line, "character": col},
            },
        )
        return self._parse_call_hierarchy_items(result)

    async def incoming_calls(self, item: CallHierarchyItem) -> list[CallHierarchyItem]:
        result = await self._request(
            "callHierarchy/incomingCalls",
            {"item": self._call_hierarchy_item_to_lsp(item)},
        )
        if not result:
            return []
        return [
            self._parse_single_call_hierarchy_item(entry["from"])
            for entry in result
            if "from" in entry
        ]

    async def outgoing_calls(self, item: CallHierarchyItem) -> list[CallHierarchyItem]:
        result = await self._request(
            "callHierarchy/outgoingCalls",
            {"item": self._call_hierarchy_item_to_lsp(item)},
        )
        if not result:
            return []
        return [
            self._parse_single_call_hierarchy_item(entry["to"])
            for entry in result
            if "to" in entry
        ]

    @staticmethod
    def _call_hierarchy_item_to_lsp(item: CallHierarchyItem) -> dict[str, Any]:
        # Prefer the original raw LSP dict so opaque server-private state
        # (`data`, `tags`, full ranges, exact `kind` int) round-trips verbatim.
        if item.raw:
            return dict(item.raw)
        kind_int = _SYMBOL_KIND_INV.get(item.kind)
        if kind_int is None:
            raise ValueError(
                f"Unknown call-hierarchy symbol kind label: {item.kind!r}"
            )
        return {
            "name": item.name,
            "kind": kind_int,
            "uri": item.file,
            "range": {
                "start": {"line": item.line, "character": item.column},
                "end": {"line": item.line, "character": item.column},
            },
            "selectionRange": {
                "start": {"line": item.line, "character": item.column},
                "end": {"line": item.line, "character": item.column},
            },
        }

    def _parse_call_hierarchy_items(self, result: Any) -> list[CallHierarchyItem]:
        if not result:
            return []
        return [self._parse_single_call_hierarchy_item(item) for item in result]

    @staticmethod
    def _parse_single_call_hierarchy_item(node: dict) -> CallHierarchyItem:
        sel = node.get("selectionRange") or node.get("range") or {}
        start = sel.get("start", {})
        return CallHierarchyItem(
            name=node.get("name", ""),
            kind=_SYMBOL_KIND.get(node.get("kind", 0), "unknown"),
            file=node.get("uri", ""),
            line=start.get("line", 0),
            column=start.get("character", 0),
            raw=dict(node),
        )

    async def get_diagnostics(self, file_uri: str) -> list[Diagnostic]:
        result = await self._request(
            "textDocument/diagnostic",
            {"textDocument": {"uri": file_uri}},
        )
        diagnostics: list[Diagnostic] = []
        items = result.get("items", [])
        for item in items:
            uri = item.get("uri", file_uri)
            for raw in item.get("diagnostics", []):
                start = raw.get("range", {}).get("start", {})
                severity_int = raw.get("severity", 1)
                severity = _SEVERITY_MAP.get(severity_int, "error")
                diagnostics.append(
                    Diagnostic(
                        file=uri,
                        line=start.get("line", 0),
                        column=start.get("character", 0),
                        severity=severity,
                        message=raw.get("message", ""),
                        source=raw.get("source", ""),
                    )
                )
        return diagnostics

    async def did_open(self, file_uri: str, text: str) -> None:
        """Send textDocument/didOpen notification (no response expected)."""
        await self._notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": file_uri,
                    "languageId": "plaintext",
                    "version": 1,
                    "text": text,
                }
            },
        )

    async def shutdown(self) -> None:
        """Send shutdown + exit, then close transport."""
        await self._request("shutdown", {})
        await self._notify("exit", {})
        await self._transport.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _request(self, method: str, params: dict[str, Any]) -> Any:
        request_id = next(self._id_counter)
        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        await self._transport.send_message(message)
        response = await self._transport.receive_message()

        if "error" in response:
            error = response["error"]
            raise RuntimeError(
                f"LSP error {error.get('code')}: {error.get('message', 'Unknown error')}"
            )
        return response.get("result")

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        """Send a JSON-RPC notification (no id, no response)."""
        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        await self._transport.send_message(message)

    def _parse_locations(self, result: Any) -> list[Location]:
        if result is None:
            return []
        if isinstance(result, dict):
            result = [result]
        locations = []
        for item in result:
            uri = item.get("uri", "")
            start = item.get("range", {}).get("start", {})
            locations.append(
                Location(
                    file=uri,
                    line=start.get("line", 0),
                    column=start.get("character", 0),
                )
            )
        return locations
