"""Tests for MCPApprovalInline widget rendering."""
from __future__ import annotations

from llm_code.tui.chat_widgets import MCPApprovalInline


def _plain(widget: MCPApprovalInline) -> str:
    return widget.render().plain  # type: ignore[attr-defined]


def test_renders_server_and_owner_and_options() -> None:
    w = MCPApprovalInline(
        server_name="github",
        owner_agent_id="persona-researcher",
        command="npx @modelcontextprotocol/server-github",
        description="needs GitHub API access",
    )
    rendered = _plain(w)
    assert "github" in rendered
    assert "persona-researcher" in rendered
    assert "npx @modelcontextprotocol/server-github" in rendered
    assert "needs GitHub API access" in rendered
    # Three options present
    assert "[y]" in rendered
    assert "[a]" in rendered
    assert "[n]" in rendered
    assert "Allow once" in rendered
    assert "Deny" in rendered
    assert "Always allow 'github'" in rendered


def test_hotkeys_styled_cyan() -> None:
    w = MCPApprovalInline(
        server_name="slack",
        owner_agent_id="swarm-1",
        command="slack-mcp",
    )
    text = w.render()
    spans = [(s.start, s.end, str(s.style)) for s in text.spans]
    # At least one bright_cyan styled span for hotkeys
    assert any("bright_cyan" in style for _, _, style in spans)


def test_description_optional() -> None:
    w = MCPApprovalInline(
        server_name="minimal",
        owner_agent_id="root-child",
        command="minimal-cmd",
        description="",
    )
    rendered = _plain(w)
    assert "minimal" in rendered
    assert "root-child" in rendered
    # Should still render the three option rows
    assert "[y]" in rendered
    assert "[a]" in rendered
    assert "[n]" in rendered
