"""Tests for multi-stage agent tool filtering (tool_categories.py)."""
from __future__ import annotations

import pytest

from llm_code.tools.tool_categories import (
    ALL_AGENT_DISALLOWED,
    ASYNC_AGENT_ALLOWED,
    CUSTOM_AGENT_DISALLOWED,
    MCP_TOOL_PREFIX,
    TEAMMATE_EXTRA_ALLOWED,
    filter_tools_for_agent,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FULL_TOOL_SET: frozenset[str] = frozenset({
    # Would-be disallowed
    "ask_user",
    "enter_plan_mode",
    "exit_plan_mode",
    "task_stop",
    "coordinator",
    # Normal tools
    "read_file",
    "write_file",
    "edit_file",
    "bash",
    "glob_search",
    "grep_search",
    "web_search",
    "web_fetch",
    "git_status",
    "git_diff",
    "git_log",
    "todo_write",
    "skill_load",
    "tool_search",
    # Agent tool (kept for depth-based recursion guard)
    "agent",
    # Custom-only disallowed
    "swarm_create",
    "swarm_delete",
    # Teammate extras
    "swarm_message",
    "task_plan",
    "task_verify",
    "task_close",
    # MCP tools
    "mcp__slack__post_message",
    "mcp__github__create_pr",
    # Interactive
    "memory_recall",
    "lsp_hover",
    "notebook_edit",
    "notebook_read",
    "multi_edit",
})


# ---------------------------------------------------------------------------
# Stage 1: MCP bypass
# ---------------------------------------------------------------------------

class TestMCPBypass:
    def test_mcp_tools_always_survive_sync_builtin(self) -> None:
        result = filter_tools_for_agent(FULL_TOOL_SET, is_builtin=True, is_async=False)
        assert "mcp__slack__post_message" in result
        assert "mcp__github__create_pr" in result

    def test_mcp_tools_always_survive_async(self) -> None:
        result = filter_tools_for_agent(FULL_TOOL_SET, is_builtin=True, is_async=True)
        assert "mcp__slack__post_message" in result

    def test_mcp_tools_always_survive_custom_async(self) -> None:
        result = filter_tools_for_agent(FULL_TOOL_SET, is_builtin=False, is_async=True)
        assert "mcp__github__create_pr" in result


# ---------------------------------------------------------------------------
# Stage 3: Global deny-list
# ---------------------------------------------------------------------------

class TestGlobalDenyList:
    def test_disallowed_tools_removed_from_sync_builtin(self) -> None:
        result = filter_tools_for_agent(FULL_TOOL_SET, is_builtin=True, is_async=False)
        for tool in ALL_AGENT_DISALLOWED:
            assert tool not in result, f"{tool} should be disallowed"

    def test_agent_tool_survives_sync_builtin(self) -> None:
        """Agent tool is intentionally NOT in ALL_AGENT_DISALLOWED."""
        result = filter_tools_for_agent(FULL_TOOL_SET, is_builtin=True, is_async=False)
        assert "agent" in result

    def test_disallowed_tools_removed_from_async(self) -> None:
        result = filter_tools_for_agent(FULL_TOOL_SET, is_builtin=True, is_async=True)
        for tool in ALL_AGENT_DISALLOWED:
            assert tool not in result


# ---------------------------------------------------------------------------
# Stage 4: Custom agent extra deny-list
# ---------------------------------------------------------------------------

class TestCustomDenyList:
    def test_custom_agents_lose_agent_tool(self) -> None:
        result = filter_tools_for_agent(FULL_TOOL_SET, is_builtin=False, is_async=False)
        assert "agent" not in result

    def test_custom_agents_lose_swarm_create(self) -> None:
        result = filter_tools_for_agent(FULL_TOOL_SET, is_builtin=False, is_async=False)
        assert "swarm_create" not in result
        assert "swarm_delete" not in result

    def test_builtin_keeps_swarm_create(self) -> None:
        result = filter_tools_for_agent(FULL_TOOL_SET, is_builtin=True, is_async=False)
        assert "swarm_create" in result


# ---------------------------------------------------------------------------
# Stage 5: Async allow-list (positive filter)
# ---------------------------------------------------------------------------

class TestAsyncAllowList:
    def test_async_only_allows_listed_tools(self) -> None:
        result = filter_tools_for_agent(FULL_TOOL_SET, is_builtin=True, is_async=True)
        non_mcp = {t for t in result if not t.startswith(MCP_TOOL_PREFIX)}
        assert non_mcp <= ASYNC_AGENT_ALLOWED, (
            f"Unexpected tools in async result: {non_mcp - ASYNC_AGENT_ALLOWED}"
        )

    def test_async_includes_file_io(self) -> None:
        result = filter_tools_for_agent(FULL_TOOL_SET, is_builtin=True, is_async=True)
        assert "read_file" in result
        assert "write_file" in result
        assert "edit_file" in result
        assert "bash" in result

    def test_async_excludes_interactive_tools(self) -> None:
        result = filter_tools_for_agent(FULL_TOOL_SET, is_builtin=True, is_async=True)
        assert "lsp_hover" not in result
        assert "memory_recall" not in result

    def test_async_excludes_agent_tool(self) -> None:
        """Agent tool is not in ASYNC_AGENT_ALLOWED → blocked for async."""
        result = filter_tools_for_agent(FULL_TOOL_SET, is_builtin=True, is_async=True)
        assert "agent" not in result


# ---------------------------------------------------------------------------
# Stage 6: Teammate extras
# ---------------------------------------------------------------------------

class TestTeammateExtras:
    def test_teammate_gets_task_tools(self) -> None:
        result = filter_tools_for_agent(
            FULL_TOOL_SET, is_builtin=True, is_async=True, is_teammate=True,
        )
        for tool in TEAMMATE_EXTRA_ALLOWED:
            assert tool in result, f"Teammate should have {tool}"

    def test_non_teammate_async_lacks_task_tools(self) -> None:
        result = filter_tools_for_agent(
            FULL_TOOL_SET, is_builtin=True, is_async=True, is_teammate=False,
        )
        for tool in TEAMMATE_EXTRA_ALLOWED:
            assert tool not in result, f"Non-teammate should lack {tool}"


# ---------------------------------------------------------------------------
# Combined: sync built-in is the most permissive
# ---------------------------------------------------------------------------

class TestSyncBuiltinIsPermissive:
    def test_sync_builtin_keeps_most_tools(self) -> None:
        result = filter_tools_for_agent(FULL_TOOL_SET, is_builtin=True, is_async=False)
        # Should keep everything except ALL_AGENT_DISALLOWED
        expected_removed = ALL_AGENT_DISALLOWED
        for name in FULL_TOOL_SET:
            if name.startswith(MCP_TOOL_PREFIX):
                assert name in result
            elif name in expected_removed:
                assert name not in result
            else:
                assert name in result, f"{name} should survive sync builtin"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_input(self) -> None:
        result = filter_tools_for_agent(frozenset())
        assert result == frozenset()

    def test_only_mcp_tools(self) -> None:
        tools = frozenset({"mcp__a", "mcp__b"})
        result = filter_tools_for_agent(tools, is_builtin=False, is_async=True)
        assert result == tools

    def test_only_disallowed_tools(self) -> None:
        result = filter_tools_for_agent(ALL_AGENT_DISALLOWED)
        assert result == frozenset()

    def test_immutability(self) -> None:
        """Input set is not mutated."""
        original = frozenset(FULL_TOOL_SET)
        filter_tools_for_agent(original, is_builtin=True, is_async=True)
        assert original == FULL_TOOL_SET


# ---------------------------------------------------------------------------
# Integration: registry.filtered() with new params
# ---------------------------------------------------------------------------

class TestRegistryIntegration:
    def _make_registry(self, names: list[str]):
        from llm_code.tools.base import PermissionLevel, Tool, ToolResult
        from llm_code.tools.registry import ToolRegistry

        class Stub(Tool):
            def __init__(self, n: str) -> None:
                self._n = n
            @property
            def name(self) -> str:
                return self._n
            @property
            def description(self) -> str:
                return ""
            @property
            def input_schema(self) -> dict:
                return {"type": "object", "properties": {}}
            @property
            def required_permission(self) -> PermissionLevel:
                return PermissionLevel.READ_ONLY
            def execute(self, args: dict) -> ToolResult:
                return ToolResult(output="")

        reg = ToolRegistry()
        for n in names:
            reg.register(Stub(n))
        return reg

    def test_registry_filtered_with_is_async(self) -> None:
        reg = self._make_registry(["read_file", "ask_user", "bash", "lsp_hover"])
        child = reg.filtered(None, is_builtin=True, is_async=True)
        names = {t.name for t in child.all_tools()}
        assert "read_file" in names
        assert "bash" in names
        assert "ask_user" not in names
        assert "lsp_hover" not in names  # not in ASYNC_AGENT_ALLOWED

    def test_registry_filtered_disallowed_param(self) -> None:
        reg = self._make_registry(["read_file", "bash", "write_file"])
        child = reg.filtered(None, disallowed=frozenset({"bash"}))
        names = {t.name for t in child.all_tools()}
        assert "read_file" in names
        assert "write_file" in names
        assert "bash" not in names

    def test_registry_filtered_custom_blocks_agent(self) -> None:
        reg = self._make_registry(["agent", "read_file", "bash"])
        child = reg.filtered(None, is_builtin=False)
        names = {t.name for t in child.all_tools()}
        assert "agent" not in names
        assert "read_file" in names

    def test_registry_filtered_mcp_always_survives(self) -> None:
        reg = self._make_registry(["mcp__test__tool", "ask_user"])
        child = reg.filtered(None, is_builtin=True, is_async=True)
        names = {t.name for t in child.all_tools()}
        assert "mcp__test__tool" in names
        assert "ask_user" not in names
