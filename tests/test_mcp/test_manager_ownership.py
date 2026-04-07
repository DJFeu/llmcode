"""Tests for per-agent ownership / approval in McpServerManager."""
from __future__ import annotations

from typing import Any

import pytest

from llm_code.mcp import manager as manager_mod
from llm_code.mcp.agent_approval import MCPApprovalRequest
from llm_code.mcp.manager import (
    MCPApprovalDeniedError,
    McpServerManager,
    ROOT_AGENT_ID,
)
from llm_code.mcp.types import McpServerConfig, McpServerInfo


class _FakeTransport:
    def __init__(self) -> None:
        self.started = False
        self.closed = False

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True


class _FakeClient:
    def __init__(self, transport: Any) -> None:
        self._transport = transport
        self.closed = False

    async def initialize(self) -> McpServerInfo:
        return McpServerInfo(name="fake", version="0", capabilities={})

    async def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _patch_transport_and_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        McpServerManager,
        "_build_transport",
        staticmethod(lambda config: _FakeTransport()),
    )
    monkeypatch.setattr(manager_mod, "McpClient", _FakeClient)


def _cfg() -> McpServerConfig:
    return McpServerConfig(command="/bin/true", args=())


@pytest.mark.asyncio
async def test_root_spawn_skips_approval() -> None:
    mgr = McpServerManager()
    client = await mgr.start_server("root-srv", _cfg())
    assert client is not None
    instance = mgr.get_instance("root-srv")
    assert instance is not None
    assert instance.owner_agent_id == ROOT_AGENT_ID
    assert "root-srv" in mgr.registry.owned_by(ROOT_AGENT_ID)


@pytest.mark.asyncio
async def test_non_root_spawn_approved() -> None:
    mgr = McpServerManager()

    async def approve(req: MCPApprovalRequest) -> bool:
        assert req.agent_id == "persona-foo"
        assert "foo-srv" in req.server_names
        return True

    await mgr.start_server("foo-srv", _cfg(), owner_agent_id="persona-foo", approval_callback=approve)
    assert mgr.get_client("foo-srv") is not None
    assert "foo-srv" in mgr.registry.owned_by("persona-foo")


@pytest.mark.asyncio
async def test_non_root_spawn_denied_raises() -> None:
    mgr = McpServerManager()

    async def deny(req: MCPApprovalRequest) -> bool:
        return False

    with pytest.raises(MCPApprovalDeniedError):
        await mgr.start_server("foo-srv", _cfg(), owner_agent_id="persona-foo", approval_callback=deny)

    assert mgr.get_client("foo-srv") is None
    assert mgr.get_instance("foo-srv") is None
    assert "foo-srv" not in mgr.registry.owned_by("persona-foo")


@pytest.mark.asyncio
async def test_cleanup_for_agent_preserves_root() -> None:
    mgr = McpServerManager()

    async def approve(_req: MCPApprovalRequest) -> bool:
        return True

    await mgr.start_server("root-srv", _cfg())
    await mgr.start_server("foo-a", _cfg(), owner_agent_id="persona-foo", approval_callback=approve)
    await mgr.start_server("foo-b", _cfg(), owner_agent_id="persona-foo", approval_callback=approve)

    stopped = await mgr.cleanup_for_agent("persona-foo")
    assert set(stopped) == {"foo-a", "foo-b"}
    assert mgr.get_client("root-srv") is not None
    assert mgr.get_client("foo-a") is None
    assert mgr.get_client("foo-b") is None
    assert mgr.registry.owned_by("persona-foo") == frozenset()
    assert "root-srv" in mgr.registry.owned_by(ROOT_AGENT_ID)


@pytest.mark.asyncio
async def test_cleanup_for_unknown_agent_is_noop() -> None:
    mgr = McpServerManager()
    stopped = await mgr.cleanup_for_agent("ghost")
    assert stopped == []


@pytest.mark.asyncio
async def test_stop_all_after_agent_cleanup() -> None:
    mgr = McpServerManager()

    async def approve(_req: MCPApprovalRequest) -> bool:
        return True

    await mgr.start_server("root-srv", _cfg())
    await mgr.start_server("foo-srv", _cfg(), owner_agent_id="persona-foo", approval_callback=approve)
    await mgr.cleanup_for_agent("persona-foo")
    await mgr.stop_all()
    assert mgr.get_client("root-srv") is None
    assert mgr.registry.all_agents() == ()
