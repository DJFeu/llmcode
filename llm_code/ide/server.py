"""WebSocket JSON-RPC server for IDE communication."""
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


class IDEServer:
    """WebSocket JSON-RPC server that IDE extensions connect to."""

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
        # Resolve actual port (important when port=0)
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
        """Send a JSON-RPC request to the first connected IDE."""
        if not self._ides:
            raise JsonRpcError(-32000, "No IDE connected")

        ide = self._ides[-1]  # most recently registered
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
        """Handle a single IDE WebSocket connection."""
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

                # If this is a response to our request (no method field)
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

                # Handle incoming methods from IDE
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
                    logger.info("IDE registered: %s (pid=%d)", ide_entry.name, ide_entry.pid)
                else:
                    await self._send_error(websocket, msg_id, -32601, f"Method not found: {method}")

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
    async def _send_error(websocket: Any, msg_id: int | None, code: int, message: str) -> None:
        await websocket.send(json.dumps({
            "jsonrpc": "2.0",
            "error": {"code": code, "message": message},
            "id": msg_id,
        }))
