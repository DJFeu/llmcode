"""Tests for IDE bridge high-level API."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_code.ide.bridge import IDEBridge
from llm_code.ide.server import JsonRpcError
from llm_code.runtime.config import IDEConfig


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

    def test_disabled_bridge_status(self, disabled_bridge: IDEBridge):
        assert disabled_bridge.is_enabled is False
        assert disabled_bridge.is_connected is False

    async def test_open_file_when_connected(self, enabled_bridge: IDEBridge):
        enabled_bridge._server.send_request.return_value = {"ok": True}
        result = await enabled_bridge.open_file("/tmp/foo.py", line=42)
        assert result is True
        enabled_bridge._server.send_request.assert_called_once_with(
            "ide/openFile", {"path": "/tmp/foo.py", "line": 42}
        )

    async def test_open_file_graceful_fallback(self, disabled_bridge: IDEBridge):
        result = await disabled_bridge.open_file("/tmp/foo.py")
        assert result is False

    async def test_get_diagnostics_when_connected(self, enabled_bridge: IDEBridge):
        enabled_bridge._server.send_request.return_value = {
            "diagnostics": [
                {"line": 5, "severity": "error", "message": "undefined var", "source": "pyright"},
            ]
        }
        diags = await enabled_bridge.get_diagnostics("/tmp/foo.py")
        assert len(diags) == 1
        assert diags[0]["severity"] == "error"

    async def test_get_diagnostics_fallback_returns_empty(self, disabled_bridge: IDEBridge):
        diags = await disabled_bridge.get_diagnostics("/tmp/foo.py")
        assert diags == []

    async def test_get_selection_when_connected(self, enabled_bridge: IDEBridge):
        enabled_bridge._server.send_request.return_value = {
            "path": "/tmp/foo.py",
            "start_line": 10,
            "end_line": 20,
            "text": "selected text",
        }
        sel = await enabled_bridge.get_selection()
        assert sel is not None
        assert sel["start_line"] == 10

    async def test_get_selection_fallback_returns_none(self, disabled_bridge: IDEBridge):
        sel = await disabled_bridge.get_selection()
        assert sel is None

    async def test_show_diff_when_connected(self, enabled_bridge: IDEBridge):
        enabled_bridge._server.send_request.return_value = {"ok": True}
        result = await enabled_bridge.show_diff("/tmp/foo.py", "old", "new")
        assert result is True

    async def test_show_diff_fallback(self, disabled_bridge: IDEBridge):
        result = await disabled_bridge.show_diff("/tmp/foo.py", "old", "new")
        assert result is False

    async def test_graceful_on_jsonrpc_error(self, enabled_bridge: IDEBridge):
        enabled_bridge._server.send_request.side_effect = JsonRpcError(-32000, "timeout")
        diags = await enabled_bridge.get_diagnostics("/tmp/x.py")
        assert diags == []

    async def test_start_stop(self):
        bridge = IDEBridge(IDEConfig(enabled=True, port=0))
        with patch("llm_code.ide.bridge.IDEServer") as mock_cls:
            mock_server = AsyncMock()
            mock_server.actual_port = 9999
            mock_cls.return_value = mock_server
            await bridge.start()
            mock_server.start.assert_called_once()
            await bridge.stop()
            mock_server.stop.assert_called_once()
