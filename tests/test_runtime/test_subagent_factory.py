"""Tests for make_subagent_runtime."""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from llm_code.runtime.subagent_factory import make_subagent_runtime
from llm_code.tools.agent_roles import BUILD_ROLE, EXPLORE_ROLE, GENERAL_ROLE
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
        return ToolResult(output="ok")


class _StubParent:
    """Minimal parent runtime stub exposing the attributes the factory reads."""
    def __init__(self, registry: ToolRegistry) -> None:
        self._provider = object()
        self._tool_registry = registry
        self._permissions = object()
        self._hooks = object()
        self._prompt_builder = object()
        self._config = object()
        self._context = object()
        # Optional attrs (factory uses getattr default None)
        self._telemetry = None
        self._cost_tracker = None


class _FakeRuntime:
    """A barebones replacement for ConversationRuntime in factory tests."""
    def __init__(self, **kwargs: Any) -> None:
        self._tool_registry = kwargs["tool_registry"]
        self.session = kwargs["session"]
        self._provider = kwargs["provider"]
        self._permissions = kwargs["permission_policy"]
        self._hooks = kwargs["hook_runner"]
        self._prompt_builder = kwargs["prompt_builder"]


@pytest.fixture
def parent() -> _StubParent:
    reg = ToolRegistry()
    for name in ("read_file", "write_file", "bash", "lsp_hover"):
        reg.register(_StubTool(name))
    return _StubParent(reg)


def _make(parent: _StubParent, role):
    with patch("llm_code.runtime.subagent_factory.ConversationRuntime", _FakeRuntime):
        return make_subagent_runtime(parent, role, model=None)


def test_subagent_uses_filtered_registry_for_explore(parent: _StubParent) -> None:
    sub = _make(parent, EXPLORE_ROLE)
    sub_names = {t.name for t in sub._tool_registry.all_tools()}
    assert "read_file" in sub_names
    assert "write_file" not in sub_names
    assert "bash" not in sub_names


def test_subagent_for_build_role_inherits_full_registry(parent: _StubParent) -> None:
    sub = _make(parent, BUILD_ROLE)
    parent_names = {t.name for t in parent._tool_registry.all_tools()}
    sub_names = {t.name for t in sub._tool_registry.all_tools()}
    # v16 M2: agent_memory_enabled defaults to True, so unrestricted
    # roles get the three memory_* tools injected on top of the
    # parent's registry. Subtract them before comparing.
    sub_names -= {"memory_read", "memory_write", "memory_list"}
    assert sub_names == parent_names


def test_subagent_for_general_role_excludes_todowrite(parent: _StubParent) -> None:
    parent._tool_registry.register(_StubTool("todowrite"))
    sub = _make(parent, GENERAL_ROLE)
    sub_names = {t.name for t in sub._tool_registry.all_tools()}
    assert "todowrite" not in sub_names
    assert "read_file" in sub_names


def test_subagent_inherits_provider_and_hooks_from_parent(parent: _StubParent) -> None:
    sub = _make(parent, EXPLORE_ROLE)
    assert sub._provider is parent._provider
    assert sub._hooks is parent._hooks
    assert sub._prompt_builder is parent._prompt_builder
    assert sub._permissions is parent._permissions


def test_subagent_records_role_on_runtime(parent: _StubParent) -> None:
    sub = _make(parent, EXPLORE_ROLE)
    assert getattr(sub, "_subagent_role", None) is EXPLORE_ROLE


def test_subagent_factory_does_not_mutate_parent_registry(parent: _StubParent) -> None:
    before = {t.name for t in parent._tool_registry.all_tools()}
    _make(parent, EXPLORE_ROLE)
    after = {t.name for t in parent._tool_registry.all_tools()}
    assert before == after


def test_subagent_factory_accepts_none_role_as_build(parent: _StubParent) -> None:
    """Calling with role=None should be equivalent to BUILD_ROLE."""
    sub = _make(parent, None)
    parent_names = {t.name for t in parent._tool_registry.all_tools()}
    sub_names = {t.name for t in sub._tool_registry.all_tools()}
    # v16 M2: subtract the three memory_* tools added by default.
    sub_names -= {"memory_read", "memory_write", "memory_list"}
    assert sub_names == parent_names
