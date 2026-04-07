"""Tests for MCP approval request value object."""
from __future__ import annotations

from llm_code.tui.mcp_approval import McpApprovalRequest, McpItem


def _req():
    return McpApprovalRequest(
        server_name="github",
        items=[
            McpItem("tool", "search_code"),
            McpItem("tool", "create_pr"),
            McpItem("resource", "issues"),
        ],
    )


def test_initial_no_approvals():
    r = _req()
    assert r.approved_items() == []


def test_approve_single():
    r = _req()
    r.approve("search_code")
    assert r.is_approved("search_code")
    assert len(r.approved_items()) == 1


def test_approve_all():
    r = _req()
    r.approve_all()
    assert len(r.approved_items()) == 3
