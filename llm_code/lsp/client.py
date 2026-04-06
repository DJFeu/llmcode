"""LSP client: types, LspTransport, and LspClient."""
from __future__ import annotations

import asyncio
import itertools
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
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
