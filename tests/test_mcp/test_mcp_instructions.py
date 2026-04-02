"""Tests for MCP instructions injection (Task 8)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from llm_code.mcp.client import McpClient
from llm_code.mcp.manager import McpServerManager
from llm_code.mcp.transport import McpTransport
from llm_code.mcp.types import McpServerConfig
from llm_code.runtime.context import ProjectContext
from llm_code.runtime.prompt import SystemPromptBuilder


# ---------------------------------------------------------------------------
# Shared mock transport (same pattern as test_client.py)
# ---------------------------------------------------------------------------

class MockTransport(McpTransport):
    """In-memory transport for testing."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.sent: list[dict[str, Any]] = []
        self._responses = list(responses)
        self.closed = False

    async def start(self) -> None:
        pass

    async def send(self, message: dict[str, Any]) -> None:
        self.sent.append(message)

    async def receive(self) -> dict[str, Any]:
        if not self._responses:
            raise RuntimeError("No more mock responses")
        return self._responses.pop(0)

    async def close(self) -> None:
        self.closed = True


def _make_init_response(request_id: int, instructions: str = "") -> dict[str, Any]:
    caps: dict[str, Any] = {}
    if instructions:
        caps["instructions"] = instructions
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "serverInfo": {"name": "test-server", "version": "1.0"},
            "capabilities": caps,
        },
    }


# ---------------------------------------------------------------------------
# McpServerManager instruction collection
# ---------------------------------------------------------------------------

class TestMcpManagerInstructions:
    @pytest.mark.asyncio
    async def test_collects_instructions(self) -> None:
        manager = McpServerManager()
        transport = MockTransport([_make_init_response(1, instructions="Do the thing.")])
        # Patch transport building so we can inject our mock
        manager._transports["srv"] = transport
        client = McpClient(transport)
        info = await client.initialize()
        manager._clients["srv"] = client

        # Extract instructions the way start_server would
        capabilities = info.capabilities or {}
        instructions = capabilities.get("instructions", "")
        if instructions:
            manager._instructions["srv"] = instructions

        result = manager.get_all_instructions()
        assert result == {"srv": "Do the thing."}

    @pytest.mark.asyncio
    async def test_no_instructions_returns_empty(self) -> None:
        manager = McpServerManager()
        transport = MockTransport([_make_init_response(1, instructions="")])
        client = McpClient(transport)
        await client.initialize()
        manager._clients["srv"] = client
        # No instructions stored

        result = manager.get_all_instructions()
        assert result == {}

    @pytest.mark.asyncio
    async def test_start_server_stores_instructions(self, monkeypatch) -> None:
        """Integration: start_server should store instructions from the initialize response."""
        manager = McpServerManager()

        transport = MockTransport([_make_init_response(1, instructions="Hello from server.")])

        # Patch _build_transport to return our mock
        monkeypatch.setattr(McpServerManager, "_build_transport", staticmethod(lambda cfg: transport))

        config = McpServerConfig(command="fake", transport_type="stdio")
        await manager.start_server("my-server", config)

        result = manager.get_all_instructions()
        assert result == {"my-server": "Hello from server."}

    @pytest.mark.asyncio
    async def test_start_server_no_instructions(self, monkeypatch) -> None:
        manager = McpServerManager()

        transport = MockTransport([_make_init_response(1, instructions="")])
        monkeypatch.setattr(McpServerManager, "_build_transport", staticmethod(lambda cfg: transport))

        config = McpServerConfig(command="fake", transport_type="stdio")
        await manager.start_server("empty-server", config)

        result = manager.get_all_instructions()
        assert result == {}


# ---------------------------------------------------------------------------
# SystemPromptBuilder MCP instructions injection
# ---------------------------------------------------------------------------

def _make_context(tmp_path: Path) -> ProjectContext:
    return ProjectContext(
        cwd=tmp_path,
        is_git_repo=False,
        git_status="",
        instructions="",
    )


class TestPromptMcpInstructions:
    def test_includes_mcp_instructions(self, tmp_path: Path) -> None:
        builder = SystemPromptBuilder()
        ctx = _make_context(tmp_path)
        prompt = builder.build(
            ctx,
            mcp_instructions={"my-server": "Use only GET requests."},
        )
        assert "my-server" in prompt
        assert "Use only GET requests." in prompt

    def test_no_mcp_instructions_omitted(self, tmp_path: Path) -> None:
        builder = SystemPromptBuilder()
        ctx = _make_context(tmp_path)
        prompt = builder.build(ctx)
        assert "MCP Server:" not in prompt

    def test_none_mcp_instructions_omitted(self, tmp_path: Path) -> None:
        builder = SystemPromptBuilder()
        ctx = _make_context(tmp_path)
        prompt = builder.build(ctx, mcp_instructions=None)
        assert "MCP Server:" not in prompt

    def test_multiple_mcp_servers(self, tmp_path: Path) -> None:
        builder = SystemPromptBuilder()
        ctx = _make_context(tmp_path)
        prompt = builder.build(
            ctx,
            mcp_instructions={
                "server-a": "Instructions A.",
                "server-b": "Instructions B.",
            },
        )
        assert "server-a" in prompt
        assert "Instructions A." in prompt
        assert "server-b" in prompt
        assert "Instructions B." in prompt
