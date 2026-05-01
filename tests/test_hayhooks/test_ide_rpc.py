"""Tests for the ported IDE JSON-RPC server (M4.11 parity)."""
from __future__ import annotations

import dataclasses
import errno
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_code.hayhooks.ide_rpc import (
    IDEBridge,
    IDEInfo,
    IDERpcServer,
    JsonRpcError,
    detect_running_ide,
)
from llm_code.runtime.config import IDEConfig


class TestIDEInfo:
    def test_frozen(self):
        info = IDEInfo(name="vscode", pid=1, workspace_path="/a")
        with pytest.raises(dataclasses.FrozenInstanceError):
            info.name = "other"  # type: ignore[misc]

    def test_fields(self):
        info = IDEInfo(name="neovim", pid=2, workspace_path="/b")
        assert info.name == "neovim"
        assert info.pid == 2


class TestDetect:
    def test_empty_when_no_ide(self):
        with patch(
            "llm_code.hayhooks.ide_rpc._iter_processes", return_value=[],
        ):
            assert detect_running_ide() == []

    def test_detects_vscode(self):
        proc = MagicMock()
        proc.pid = 10
        proc.info = {"name": "code", "cmdline": ["code", "/project"]}
        with patch(
            "llm_code.hayhooks.ide_rpc._iter_processes", return_value=[proc],
        ):
            result = detect_running_ide()
        assert len(result) == 1
        assert result[0].name == "vscode"

    def test_detects_neovim(self):
        proc = MagicMock()
        proc.pid = 20
        proc.info = {"name": "nvim", "cmdline": ["nvim", "/tmp"]}
        with patch(
            "llm_code.hayhooks.ide_rpc._iter_processes", return_value=[proc],
        ):
            result = detect_running_ide()
        assert result[0].name == "neovim"

    def test_empty_when_psutil_missing(self):
        with patch(
            "llm_code.hayhooks.ide_rpc._iter_processes", side_effect=ImportError,
        ):
            assert detect_running_ide() == []


class TestIDERpcServer:
    @pytest.fixture
    async def server(self):
        srv = IDERpcServer(port=0)
        try:
            await srv.start()
        except PermissionError as exc:
            if exc.errno == errno.EPERM:
                pytest.skip("local TCP bind is blocked in this test environment")
            raise
        yield srv
        await srv.stop()

    async def test_start_stop(self, server: IDERpcServer):
        assert server.is_running

    async def test_register_ide(self, server: IDERpcServer):
        import websockets

        url = f"ws://localhost:{server.actual_port}"
        async with websockets.connect(url) as ws:
            await ws.send(json.dumps({
                "jsonrpc": "2.0",
                "method": "ide/register",
                "params": {"name": "vscode", "pid": 1, "workspace_path": "/p"},
                "id": 1,
            }))
            resp = json.loads(await ws.recv())
            assert resp["result"]["ok"] is True

        assert len(server.connected_ides) == 1
        assert server.connected_ides[0].name == "vscode"

    async def test_unknown_method_error(self, server: IDERpcServer):
        import websockets

        url = f"ws://localhost:{server.actual_port}"
        async with websockets.connect(url) as ws:
            await ws.send(json.dumps({
                "jsonrpc": "2.0",
                "method": "does/not/exist",
                "params": {},
                "id": 7,
            }))
            resp = json.loads(await ws.recv())
            assert resp["error"]["code"] == -32601

    async def test_send_request_raises_when_no_ide(self, server: IDERpcServer):
        with pytest.raises(JsonRpcError, match="No IDE connected"):
            await server.send_request("ide/openFile", {"path": "/x"})


class TestIDEBridge:
    @pytest.fixture
    def disabled_bridge(self):
        return IDEBridge(IDEConfig(enabled=False))

    @pytest.fixture
    def enabled_bridge(self):
        bridge = IDEBridge(IDEConfig(enabled=True, port=0))
        bridge._server = MagicMock()
        bridge._server.is_running = True
        bridge._server.send_request = AsyncMock()
        bridge._server.connected_ides = [MagicMock(name="vscode")]
        return bridge

    def test_disabled_status(self, disabled_bridge: IDEBridge):
        assert disabled_bridge.is_enabled is False
        assert disabled_bridge.is_connected is False

    async def test_open_file_connected(self, enabled_bridge: IDEBridge):
        enabled_bridge._server.send_request.return_value = {"ok": True}
        ok = await enabled_bridge.open_file("/p", line=42)
        assert ok is True

    async def test_open_file_fallback(self, disabled_bridge: IDEBridge):
        assert await disabled_bridge.open_file("/p") is False

    async def test_get_diagnostics_connected(self, enabled_bridge: IDEBridge):
        enabled_bridge._server.send_request.return_value = {
            "diagnostics": [{"severity": "error", "line": 1}]
        }
        diags = await enabled_bridge.get_diagnostics("/p")
        assert diags[0]["severity"] == "error"

    async def test_get_diagnostics_fallback_empty(self, disabled_bridge):
        assert await disabled_bridge.get_diagnostics("/p") == []

    async def test_get_selection_fallback_none(self, disabled_bridge):
        assert await disabled_bridge.get_selection() is None

    async def test_show_diff_connected(self, enabled_bridge: IDEBridge):
        enabled_bridge._server.send_request.return_value = {"ok": True}
        assert await enabled_bridge.show_diff("/p", "a", "b") is True

    async def test_graceful_on_jsonrpc_error(self, enabled_bridge: IDEBridge):
        enabled_bridge._server.send_request.side_effect = JsonRpcError(-32000, "timeout")
        assert await enabled_bridge.get_diagnostics("/p") == []

    async def test_start_stop(self):
        bridge = IDEBridge(IDEConfig(enabled=True, port=0))
        with patch("llm_code.hayhooks.ide_rpc.IDERpcServer") as mock_cls:
            mock_server = AsyncMock()
            mock_server.actual_port = 9999
            mock_cls.return_value = mock_server
            await bridge.start()
            mock_server.start.assert_called_once()
            await bridge.stop()
            mock_server.stop.assert_called_once()


class TestFastApiMount:
    def test_register_ide_routes(self):
        pytest.importorskip("fastapi")
        from fastapi import FastAPI

        app = FastAPI()
        from llm_code.hayhooks.ide_rpc import register_ide_routes
        register_ide_routes(app)
        routes = [r for r in app.routes if getattr(r, "path", "") == "/ide/rpc"]
        assert routes, "/ide/rpc WebSocket route was not registered"
