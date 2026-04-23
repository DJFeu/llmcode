"""IDE JSON-RPC — migrated from ``llm_code/ide/server.py`` (M4.11).

Keeps the original wire format (JSON-RPC 2.0 over WebSocket) so that
any third-party IDE extension that already speaks llmcode's IDE
protocol only needs to update its base URL.

Standalone :class:`IDERpcServer` still uses the ``websockets`` package
so existing callers (``IDEBridge``, CLI ``/ide`` command, tool classes)
keep working unchanged. When hayhooks is run as a FastAPI app, the
same protocol is also mounted at ``/ide/rpc`` via
:func:`register_ide_routes`.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_PORT = 9876


class JsonRpcError(Exception):
    """Raised when a JSON-RPC operation fails."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class _ConnectedIDE:
    name: str
    pid: int
    workspace_path: str
    websocket: Any  # websockets.WebSocketServerProtocol


class IDERpcServer:
    """WebSocket JSON-RPC server that IDE extensions connect to.

    Behaviour is bit-for-bit compatible with the pre-M4.11
    ``llm_code.ide.server.IDEServer`` class. The rename to
    ``IDERpcServer`` reflects its new home under
    ``llm_code.hayhooks``.
    """

    def __init__(self, port: int = _DEFAULT_PORT) -> None:
        self._port = port
        self._server: Any | None = None
        self._ides: list[_ConnectedIDE] = []
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 1000
        self._actual_port: int | None = None

    @property
    def is_running(self) -> bool:
        return self._server is not None

    @property
    def actual_port(self) -> int:
        if self._actual_port is None:
            raise RuntimeError("Server not started")
        return self._actual_port

    @property
    def connected_ides(self) -> list[_ConnectedIDE]:
        return list(self._ides)

    async def start(self) -> None:
        import websockets

        self._server = await websockets.serve(
            self._handle_connection,
            "127.0.0.1",
            self._port,
        )
        for sock in self._server.sockets:
            addr = sock.getsockname()
            self._actual_port = addr[1]
            break

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self._ides.clear()
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(JsonRpcError(-32000, "Server shutting down"))
        self._pending.clear()

    async def send_request(self, method: str, params: dict) -> dict:
        """Send a JSON-RPC request to the most recently registered IDE."""
        if not self._ides:
            raise JsonRpcError(-32000, "No IDE connected")

        ide = self._ides[-1]
        req_id = self._next_id
        self._next_id += 1

        msg = json.dumps({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": req_id,
        })

        fut: asyncio.Future[dict] = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut

        try:
            await ide.websocket.send(msg)
            return await asyncio.wait_for(fut, timeout=10.0)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise JsonRpcError(-32000, f"IDE did not respond to {method} within 10s")

    async def _handle_connection(self, websocket: Any) -> None:
        ide_entry: _ConnectedIDE | None = None
        try:
            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await self._send_error(websocket, None, -32700, "Parse error")
                    continue

                msg_id = msg.get("id")
                method = msg.get("method")

                # Response to our outgoing request (no method field).
                if method is None and msg_id is not None:
                    fut = self._pending.pop(msg_id, None)
                    if fut is not None and not fut.done():
                        if "error" in msg:
                            fut.set_exception(JsonRpcError(
                                msg["error"].get("code", -32000),
                                msg["error"].get("message", "Unknown error"),
                            ))
                        else:
                            fut.set_result(msg.get("result", {}))
                    continue

                if method == "ide/register":
                    params = msg.get("params", {})
                    ide_entry = _ConnectedIDE(
                        name=params.get("name", "unknown"),
                        pid=params.get("pid", 0),
                        workspace_path=params.get("workspace_path", ""),
                        websocket=websocket,
                    )
                    self._ides.append(ide_entry)
                    await self._send_result(websocket, msg_id, {"ok": True})
                    logger.info(
                        "IDE registered: %s (pid=%d)",
                        ide_entry.name,
                        ide_entry.pid,
                    )
                else:
                    await self._send_error(
                        websocket, msg_id, -32601, f"Method not found: {method}",
                    )

        except Exception:
            logger.debug("IDE connection closed", exc_info=True)
        finally:
            if ide_entry is not None and ide_entry in self._ides:
                self._ides.remove(ide_entry)

    @staticmethod
    async def _send_result(websocket: Any, msg_id: int | None, result: dict) -> None:
        await websocket.send(json.dumps({
            "jsonrpc": "2.0",
            "result": result,
            "id": msg_id,
        }))

    @staticmethod
    async def _send_error(
        websocket: Any, msg_id: int | None, code: int, message: str,
    ) -> None:
        await websocket.send(json.dumps({
            "jsonrpc": "2.0",
            "error": {"code": code, "message": message},
            "id": msg_id,
        }))


# --- Bridge -----------------------------------------------------------


class IDEBridge:
    """High-level API for IDE communication. Degrades silently when disconnected.

    Migrated from ``llm_code.ide.bridge.IDEBridge``; the public API is
    unchanged (tests re-imported via a compat shim still pass).
    """

    def __init__(self, config: Any) -> None:
        self._config = config
        self._server: IDERpcServer | None = None

    @property
    def is_enabled(self) -> bool:
        return bool(getattr(self._config, "enabled", False))

    @property
    def is_connected(self) -> bool:
        if self._server is None:
            return False
        return self._server.is_running and len(self._server.connected_ides) > 0

    async def start(self) -> None:
        if not self.is_enabled:
            return
        self._server = IDERpcServer(port=int(getattr(self._config, "port", _DEFAULT_PORT)))
        await self._server.start()
        logger.info("IDE bridge listening on port %d", self._server.actual_port)

    async def stop(self) -> None:
        if self._server is not None:
            await self._server.stop()
            self._server = None

    async def open_file(self, path: str, line: int | None = None) -> bool:
        params: dict[str, Any] = {"path": path}
        if line is not None:
            params["line"] = line
        result = await self._safe_request("ide/openFile", params)
        return result is not None and result.get("ok", False)

    async def get_diagnostics(self, path: str) -> list[dict]:
        result = await self._safe_request("ide/diagnostics", {"path": path})
        if result is None:
            return []
        return result.get("diagnostics", [])

    async def get_selection(self) -> dict | None:
        return await self._safe_request("ide/selection", {})

    async def show_diff(self, path: str, old_text: str, new_text: str) -> bool:
        result = await self._safe_request("ide/showDiff", {
            "path": path,
            "old_text": old_text,
            "new_text": new_text,
        })
        return result is not None and result.get("ok", False)

    async def _safe_request(self, method: str, params: dict) -> dict | None:
        if self._server is None or not self._server.is_running:
            return None
        try:
            return await self._server.send_request(method, params)
        except (JsonRpcError, OSError, Exception) as exc:  # noqa: BLE001
            logger.debug("IDE request %s failed: %s", method, exc)
            return None


# --- Process detection (ported verbatim from llm_code/ide/detector.py)


@dataclass(frozen=True)
class IDEInfo:
    name: str
    pid: int
    workspace_path: str


_IDE_PATTERNS: dict[str, str] = {
    "code": "vscode",
    "code-insiders": "vscode",
    "cursor": "vscode",
    "nvim": "neovim",
    "neovim": "neovim",
    "idea": "jetbrains",
    "pycharm": "jetbrains",
    "webstorm": "jetbrains",
    "goland": "jetbrains",
    "clion": "jetbrains",
    "rubymine": "jetbrains",
    "rider": "jetbrains",
    "phpstorm": "jetbrains",
    "datagrip": "jetbrains",
    "subl": "sublime",
    "sublime_text": "sublime",
}


def _iter_processes() -> list:
    import psutil  # optional dependency
    return list(psutil.process_iter(["name", "cmdline"]))


def _extract_workspace(cmdline: list[str]) -> str:
    for arg in reversed(cmdline):
        if arg.startswith("/") and not arg.startswith("--"):
            return arg
    return ""


def detect_running_ide() -> list[IDEInfo]:
    """Scan process list for known IDEs. Returns empty list on failure."""
    try:
        procs = _iter_processes()
    except (ImportError, OSError):
        return []

    results: list[IDEInfo] = []
    for proc in procs:
        try:
            info = proc.info
            name = (info.get("name") or "").lower()
            cmdline = info.get("cmdline") or []
        except (AttributeError, KeyError):
            continue

        ide_name = _IDE_PATTERNS.get(name)
        if ide_name is None:
            continue

        workspace = _extract_workspace(cmdline)
        results.append(IDEInfo(
            name=ide_name,
            pid=proc.pid,
            workspace_path=workspace,
        ))

    return results


# --- FastAPI mount ----------------------------------------------------


def register_ide_routes(app: Any) -> None:
    """Attach a WebSocket endpoint at ``/ide/rpc`` running the JSON-RPC loop.

    The handler is self-contained so it can be mounted onto the
    OpenAI-compat FastAPI app without pulling in a running websockets
    server.
    """
    try:
        from fastapi import WebSocket, WebSocketDisconnect
    except ImportError:
        return

    async def _handle_single_connection(ws: WebSocket) -> None:
        await ws.accept()
        # Each /ide/rpc connection is its own tiny server — we don't
        # maintain a shared registry here because FastAPI clients tend
        # to treat each WS connection as an independent session.
        try:
            async for raw in ws.iter_text():
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await ws.send_text(json.dumps({
                        "jsonrpc": "2.0",
                        "error": {"code": -32700, "message": "Parse error"},
                        "id": None,
                    }))
                    continue
                method = msg.get("method")
                msg_id = msg.get("id")
                if method == "ide/register":
                    await ws.send_text(json.dumps({
                        "jsonrpc": "2.0",
                        "result": {"ok": True},
                        "id": msg_id,
                    }))
                elif method is None:
                    # Response from IDE — no server-initiated requests
                    # flow through this simple endpoint, so silently drop.
                    continue
                else:
                    await ws.send_text(json.dumps({
                        "jsonrpc": "2.0",
                        "error": {"code": -32601, "message": f"Method not found: {method}"},
                        "id": msg_id,
                    }))
        except WebSocketDisconnect:
            return

    app.add_api_websocket_route("/ide/rpc", _handle_single_connection)
