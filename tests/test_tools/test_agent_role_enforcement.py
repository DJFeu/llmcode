"""End-to-end: subagent role whitelist actually enforces tool exclusions."""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

from llm_code.runtime.subagent_factory import make_subagent_runtime
from llm_code.tools.agent_roles import (
    BUILD_ROLE,
    EXPLORE_ROLE,
    GENERAL_ROLE,
    PLAN_ROLE,
)
from llm_code.tools.base import PermissionLevel, Tool, ToolResult
from llm_code.tools.registry import ToolRegistry


class _StubTool(Tool):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._name

    @property
    def input_schema(self) -> dict:
        return {"type": "object"}

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    def execute(self, args: dict) -> ToolResult:
        return ToolResult(output=self._name)


class _FakeParent:
    def __init__(self, registry: ToolRegistry) -> None:
        self._provider = object()
        self._tool_registry = registry
        self._permissions = object()
        self._hooks = object()
        self._prompt_builder = object()
        self._config = object()
        self._context = object()


class _FakeRuntime:
    def __init__(self, **kwargs: Any) -> None:
        self._tool_registry = kwargs["tool_registry"]
        self.session = kwargs["session"]


def _build_parent_with_full_toolset() -> _FakeParent:
    reg = ToolRegistry()
    for name in (
        "read_file", "write_file", "edit_file", "multi_edit",
        "bash", "grep_search", "glob_search",
        "lsp_hover", "lsp_diagnostics",
        "todowrite", "web_fetch",
    ):
        reg.register(_StubTool(name))
    return _FakeParent(reg)


def _make(parent, role):
    with patch("llm_code.runtime.subagent_factory.ConversationRuntime", _FakeRuntime):
        return make_subagent_runtime(parent, role, model=None)


def test_explore_subagent_cannot_see_write_tools() -> None:
    parent = _build_parent_with_full_toolset()
    sub = _make(parent, EXPLORE_ROLE)
    assert sub._tool_registry.get("write_file") is None
    assert sub._tool_registry.get("edit_file") is None
    assert sub._tool_registry.get("bash") is None
    assert sub._tool_registry.get("read_file") is not None


def test_plan_subagent_cannot_see_exec_tools() -> None:
    parent = _build_parent_with_full_toolset()
    sub = _make(parent, PLAN_ROLE)
    assert sub._tool_registry.get("bash") is None
    assert sub._tool_registry.get("write_file") is None
    assert sub._tool_registry.get("read_file") is not None


def test_general_subagent_cannot_see_todowrite() -> None:
    parent = _build_parent_with_full_toolset()
    sub = _make(parent, GENERAL_ROLE)
    assert sub._tool_registry.get("todowrite") is None
    assert sub._tool_registry.get("bash") is not None
    assert sub._tool_registry.get("write_file") is not None


def test_build_subagent_inherits_full_registry() -> None:
    parent = _build_parent_with_full_toolset()
    sub = _make(parent, BUILD_ROLE)
    parent_names = {t.name for t in parent._tool_registry.all_tools()}
    sub_names = {t.name for t in sub._tool_registry.all_tools()}
    assert sub_names == parent_names


def test_subagent_filtering_does_not_leak_into_parent() -> None:
    parent = _build_parent_with_full_toolset()
    parent_names_before = {t.name for t in parent._tool_registry.all_tools()}
    _make(parent, EXPLORE_ROLE)
    _make(parent, PLAN_ROLE)
    _make(parent, GENERAL_ROLE)
    parent_names_after = {t.name for t in parent._tool_registry.all_tools()}
    assert parent_names_before == parent_names_after


# ---------------------------------------------------------------------------
# Issue 1: BUILD_ROLE recursion-depth bypass
# ---------------------------------------------------------------------------


def _parent_with_real_agent_tool(max_depth: int) -> _FakeParent:
    from llm_code.tools.agent import AgentTool

    reg = ToolRegistry()
    reg.register(_StubTool("read_file"))
    reg.register(
        AgentTool(
            runtime_factory=lambda model, role=None: None,
            max_depth=max_depth,
            current_depth=0,
        )
    )
    return _FakeParent(reg)


def test_build_role_child_increments_agent_tool_depth() -> None:
    from llm_code.tools.agent import AgentTool

    parent = _parent_with_real_agent_tool(max_depth=3)
    child = _make(parent, BUILD_ROLE)
    child_agent = child._tool_registry.get("agent")
    assert isinstance(child_agent, AgentTool)
    assert child_agent._current_depth == 1
    # Parent's AgentTool depth must be untouched.
    assert parent._tool_registry.get("agent")._current_depth == 0


def test_build_role_grandchild_depth_chain() -> None:
    from llm_code.tools.agent import AgentTool

    parent = _parent_with_real_agent_tool(max_depth=3)
    child = _make(parent, BUILD_ROLE)
    grand = _make(_FakeParent(child._tool_registry), BUILD_ROLE)
    g_agent = grand._tool_registry.get("agent")
    assert isinstance(g_agent, AgentTool)
    assert g_agent._current_depth == 2


def test_build_role_great_grandchild_refused_by_depth_guard() -> None:
    from llm_code.tools.agent import AgentTool
    from llm_code.tools.base import ToolResult

    parent = _parent_with_real_agent_tool(max_depth=3)
    child = _make(parent, BUILD_ROLE)
    grand = _make(_FakeParent(child._tool_registry), BUILD_ROLE)
    great = _make(_FakeParent(grand._tool_registry), BUILD_ROLE)
    great_agent = great._tool_registry.get("agent")
    assert isinstance(great_agent, AgentTool)
    assert great_agent._current_depth == 3
    # Now invoking it should hit the depth guard.
    result: ToolResult = great_agent.execute({"task": "noop"})
    assert result.is_error is True
    assert "depth" in result.output.lower()


# ---------------------------------------------------------------------------
# Issue 3: dispatch-time defense-in-depth via is_tool_allowed_for_role
# ---------------------------------------------------------------------------


def test_dispatch_blocks_disallowed_tool_even_if_registry_leaks() -> None:
    """Simulate a registry leak: parent registry contains a forbidden tool,
    but the role check at _execute_tool_with_streaming refuses dispatch."""
    import asyncio

    from llm_code.api.types import ToolResultBlock
    from llm_code.runtime.conversation import ConversationRuntime
    from llm_code.tools.agent_roles import EXPLORE_ROLE
    from llm_code.tools.parsing import ParsedToolCall

    # Build a registry that LEAKS bash into an explore-role child.
    reg = ToolRegistry()
    reg.register(_StubTool("read_file"))
    reg.register(_StubTool("bash"))  # leaked!

    # Bypass __init__ — we only need the dispatch path + a couple of attrs.
    runtime = ConversationRuntime.__new__(ConversationRuntime)
    runtime._tool_registry = reg
    runtime._subagent_role = EXPLORE_ROLE

    # Stub the hook firing so it doesn't blow up on a missing hook runner.
    runtime._fire_hook = lambda *a, **k: None

    call = ParsedToolCall(id="t1", name="bash", args={}, source="native")

    async def _drive():
        results = []
        async for ev in runtime._execute_tool_with_streaming(call):
            results.append(ev)
        return results

    events = asyncio.run(_drive())
    blocks = [e for e in events if isinstance(e, ToolResultBlock)]
    assert len(blocks) == 1
    assert blocks[0].is_error is True
    assert "explore" in blocks[0].content.lower() or "permitted" in blocks[0].content.lower()
