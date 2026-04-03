"""Tests for IDE WebSocket JSON-RPC server."""
from __future__ import annotations

import asyncio
import json

import pytest

from llm_code.ide.server import IDEServer, JsonRpcError


class TestIDEServer:
    @pytest.fixture
    async def server(self):
        srv = IDEServer(port=0)  # port=0 -> OS picks free port
        await srv.start()
        yield srv
        await srv.stop()

    @pytest.fixture
    def ws_url(self, server: IDEServer) -> str:
        return f"ws://localhost:{server.actual_port}"

    async def test_start_stop(self, server: IDEServer):
        assert server.is_running

    async def test_register_ide(self, server: IDEServer, ws_url: str):
        import websockets
        async with websockets.connect(ws_url) as ws:
            await ws.send(json.dumps({
                "jsonrpc": "2.0",
                "method": "ide/register",
                "params": {"name": "vscode", "pid": 1234, "workspace_path": "/tmp"},
                "id": 1,
            }))
            resp = json.loads(await ws.recv())
            assert resp["result"]["ok"] is True
            assert resp["id"] == 1

        assert len(server.connected_ides) == 1
        assert server.connected_ides[0].name == "vscode"

    async def test_unknown_method_returns_error(self, server: IDEServer, ws_url: str):
        import websockets
        async with websockets.connect(ws_url) as ws:
            await ws.send(json.dumps({
                "jsonrpc": "2.0",
                "method": "unknown/method",
                "params": {},
                "id": 2,
            }))
            resp = json.loads(await ws.recv())
            assert "error" in resp
            assert resp["error"]["code"] == -32601

    async def test_send_request_to_ide(self, server: IDEServer, ws_url: str):
        import websockets
        async with websockets.connect(ws_url) as ws:
            # Register first
            await ws.send(json.dumps({
                "jsonrpc": "2.0",
                "method": "ide/register",
                "params": {"name": "vscode", "pid": 1, "workspace_path": "/tmp"},
                "id": 1,
            }))
            await ws.recv()  # consume register response

            # Now the server sends a request to the IDE (simulate via bridge)
            async def respond():
                msg = json.loads(await ws.recv())
                assert msg["method"] == "ide/diagnostics"
                await ws.send(json.dumps({
                    "jsonrpc": "2.0",
                    "result": {"diagnostics": [
                        {"line": 10, "severity": "error", "message": "syntax error", "source": "pyright"},
                    ]},
                    "id": msg["id"],
                }))

            responder = asyncio.create_task(respond())
            result = await server.send_request("ide/diagnostics", {"path": "/tmp/foo.py"})
            await responder
            assert len(result["diagnostics"]) == 1
            assert result["diagnostics"][0]["severity"] == "error"

    async def test_send_request_no_ide_raises(self, server: IDEServer):
        with pytest.raises(JsonRpcError, match="No IDE connected"):
            await server.send_request("ide/openFile", {"path": "/tmp/x.py"})

    async def test_multiple_register_tracks_latest(self, server: IDEServer, ws_url: str):
        import websockets
        async with websockets.connect(ws_url) as ws1:
            await ws1.send(json.dumps({
                "jsonrpc": "2.0",
                "method": "ide/register",
                "params": {"name": "vscode", "pid": 1, "workspace_path": "/a"},
                "id": 1,
            }))
            await ws1.recv()

            async with websockets.connect(ws_url) as ws2:
                await ws2.send(json.dumps({
                    "jsonrpc": "2.0",
                    "method": "ide/register",
                    "params": {"name": "neovim", "pid": 2, "workspace_path": "/b"},
                    "id": 1,
                }))
                await ws2.recv()
                assert len(server.connected_ides) == 2
