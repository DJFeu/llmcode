"""Tests for ConversationRuntime.request_mcp_approval sink-based flow."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from llm_code.api.types import StreamMCPApprovalRequest
from llm_code.mcp.agent_approval import MCPApprovalRequest

from tests.test_runtime.test_conversation import MockProvider, _make_runtime


def _req(name: str = "github") -> MCPApprovalRequest:
    return MCPApprovalRequest(
        agent_id="persona-1",
        agent_name="persona-1",
        server_names=(name,),
        reason=f"spawn MCP server '{name}'",
    )


@pytest.mark.asyncio
async def test_default_deny_without_sink(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path, MockProvider([]))
    assert await runtime.request_mcp_approval(_req()) is False


@pytest.mark.asyncio
async def test_allow_response(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path, MockProvider([]))
    events: list[StreamMCPApprovalRequest] = []

    def sink(event: StreamMCPApprovalRequest) -> None:
        events.append(event)
        asyncio.get_event_loop().call_soon(
            runtime.send_mcp_approval_response, "allow",
        )

    runtime.set_mcp_event_sink(sink)
    result = await runtime.request_mcp_approval(_req("gh"))
    assert result is True
    assert len(events) == 1
    assert events[0].server_name == "gh"
    assert events[0].owner_agent_id == "persona-1"
    # "allow" is single-shot — should NOT be added to session allowlist
    assert "gh" not in runtime._mcp_approved_servers


@pytest.mark.asyncio
async def test_always_response_adds_to_allowlist(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path, MockProvider([]))

    def sink(event: StreamMCPApprovalRequest) -> None:
        asyncio.get_event_loop().call_soon(
            runtime.send_mcp_approval_response, "always",
        )

    runtime.set_mcp_event_sink(sink)
    assert await runtime.request_mcp_approval(_req("slack")) is True
    assert "slack" in runtime._mcp_approved_servers


@pytest.mark.asyncio
async def test_allowlist_short_circuits_subsequent_calls(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path, MockProvider([]))
    runtime._mcp_approved_servers.add("github")
    call_count = 0

    def sink(event: StreamMCPApprovalRequest) -> None:
        nonlocal call_count
        call_count += 1
        asyncio.get_event_loop().call_soon(
            runtime.send_mcp_approval_response, "deny",
        )

    runtime.set_mcp_event_sink(sink)
    assert await runtime.request_mcp_approval(_req("github")) is True
    assert call_count == 0  # sink never called


@pytest.mark.asyncio
async def test_deny_response(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path, MockProvider([]))

    def sink(event: StreamMCPApprovalRequest) -> None:
        asyncio.get_event_loop().call_soon(
            runtime.send_mcp_approval_response, "deny",
        )

    runtime.set_mcp_event_sink(sink)
    assert await runtime.request_mcp_approval(_req()) is False


@pytest.mark.asyncio
async def test_timeout_defaults_to_deny(tmp_path: Path, monkeypatch) -> None:
    runtime = _make_runtime(tmp_path, MockProvider([]))

    def sink(event: StreamMCPApprovalRequest) -> None:
        pass  # never respond

    runtime.set_mcp_event_sink(sink)

    async def fake_wait_for(fut, timeout):
        if hasattr(fut, "cancel"):
            fut.cancel()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
    assert await runtime.request_mcp_approval(_req()) is False
