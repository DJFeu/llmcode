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
