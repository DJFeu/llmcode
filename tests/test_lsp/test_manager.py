"""Tests for LspServerManager (Task 3)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_code.lsp.client import LspClient, LspServerConfig
from llm_code.lsp.manager import LspServerManager


def make_mock_client() -> MagicMock:
    client = MagicMock(spec=LspClient)
    client.initialize = AsyncMock(return_value={"capabilities": {}})
    client.shutdown = AsyncMock()
    return client


def make_mock_transport() -> MagicMock:
    transport = MagicMock()
    transport.start = AsyncMock()
    return transport


class TestLspServerManagerStartServer:
    @pytest.mark.asyncio
    async def test_start_server_returns_client(self, tmp_path: Path):
        config = LspServerConfig(command="pyright-langserver", args=("--stdio",), language="python")
        mock_transport = make_mock_transport()
        mock_client = make_mock_client()

        with patch("llm_code.lsp.manager.StdioLspTransport", return_value=mock_transport), \
             patch("llm_code.lsp.manager.LspClient", return_value=mock_client):
            manager = LspServerManager()
            client = await manager.start_server("python", config, tmp_path)

        assert client is mock_client
        mock_transport.start.assert_awaited_once()
        mock_client.initialize.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_server_stores_client(self, tmp_path: Path):
        config = LspServerConfig(command="gopls", args=("serve",), language="go")
        mock_transport = make_mock_transport()
        mock_client = make_mock_client()

        with patch("llm_code.lsp.manager.StdioLspTransport", return_value=mock_transport), \
             patch("llm_code.lsp.manager.LspClient", return_value=mock_client):
            manager = LspServerManager()
            await manager.start_server("go", config, tmp_path)

        assert manager.get_client("go") is mock_client


class TestLspServerManagerGetClient:
    @pytest.mark.asyncio
    async def test_get_client_returns_none_if_not_started(self):
        manager = LspServerManager()
        assert manager.get_client("python") is None

    @pytest.mark.asyncio
    async def test_get_client_returns_correct_client(self, tmp_path: Path):
        config_py = LspServerConfig(command="pyright-langserver", args=("--stdio",), language="python")
        config_go = LspServerConfig(command="gopls", args=("serve",), language="go")

        py_client = make_mock_client()
        go_client = make_mock_client()
        clients_iter = iter([py_client, go_client])

        def make_client(_transport):
            return next(clients_iter)

        with patch("llm_code.lsp.manager.StdioLspTransport", return_value=make_mock_transport()), \
             patch("llm_code.lsp.manager.LspClient", side_effect=make_client):
            manager = LspServerManager()
            await manager.start_server("python", config_py, tmp_path)
            await manager.start_server("go", config_go, tmp_path)

        assert manager.get_client("python") is py_client
        assert manager.get_client("go") is go_client
        assert manager.get_client("rust") is None


class TestLspServerManagerStartAll:
    @pytest.mark.asyncio
    async def test_start_all_starts_all_configs(self, tmp_path: Path):
        configs = {
            "python": LspServerConfig(command="pyright-langserver", args=("--stdio",), language="python"),
            "go": LspServerConfig(command="gopls", args=("serve",), language="go"),
        }
        mock_transport = make_mock_transport()
        def make_client(_transport):
            client = make_mock_client()
            return client

        with patch("llm_code.lsp.manager.StdioLspTransport", return_value=mock_transport), \
             patch("llm_code.lsp.manager.LspClient", side_effect=make_client):
            manager = LspServerManager()
            await manager.start_all(configs, tmp_path)

        assert manager.get_client("python") is not None
        assert manager.get_client("go") is not None


class TestLspServerManagerStopAll:
    @pytest.mark.asyncio
    async def test_stop_all_shuts_down_clients(self, tmp_path: Path):
        config = LspServerConfig(command="pyright-langserver", args=("--stdio",), language="python")
        mock_transport = make_mock_transport()
        mock_client = make_mock_client()

        with patch("llm_code.lsp.manager.StdioLspTransport", return_value=mock_transport), \
             patch("llm_code.lsp.manager.LspClient", return_value=mock_client):
            manager = LspServerManager()
            await manager.start_server("python", config, tmp_path)
            await manager.stop_all()

        mock_client.shutdown.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_all_clears_clients(self, tmp_path: Path):
        config = LspServerConfig(command="pyright-langserver", args=("--stdio",), language="python")
        mock_transport = make_mock_transport()
        mock_client = make_mock_client()

        with patch("llm_code.lsp.manager.StdioLspTransport", return_value=mock_transport), \
             patch("llm_code.lsp.manager.LspClient", return_value=mock_client):
            manager = LspServerManager()
            await manager.start_server("python", config, tmp_path)
            await manager.stop_all()

        assert manager.get_client("python") is None

    @pytest.mark.asyncio
    async def test_stop_all_handles_shutdown_errors(self, tmp_path: Path):
        config = LspServerConfig(command="pyright-langserver", args=("--stdio",), language="python")
        mock_transport = make_mock_transport()
        mock_client = make_mock_client()
        mock_client.shutdown = AsyncMock(side_effect=RuntimeError("already dead"))

        with patch("llm_code.lsp.manager.StdioLspTransport", return_value=mock_transport), \
             patch("llm_code.lsp.manager.LspClient", return_value=mock_client):
            manager = LspServerManager()
            await manager.start_server("python", config, tmp_path)
            # Should not raise
            await manager.stop_all()
