"""ToolRegistry.filtered() tests."""
from __future__ import annotations

import pytest

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
        return f"stub {self._name}"

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    def execute(self, args: dict) -> ToolResult:
        return ToolResult(output=f"ran {self._name}")


@pytest.fixture
def populated_registry() -> ToolRegistry:
    reg = ToolRegistry()
    for name in ("read_file", "write_file", "bash", "lsp_hover"):
        reg.register(_StubTool(name))
    return reg


def test_filtered_returns_new_registry_instance(populated_registry: ToolRegistry) -> None:
    child = populated_registry.filtered({"read_file"})
    assert child is not populated_registry
    assert isinstance(child, ToolRegistry)


def test_filtered_only_contains_allowed_tools(populated_registry: ToolRegistry) -> None:
    child = populated_registry.filtered({"read_file", "lsp_hover"})
    names = {t.name for t in child.all_tools()}
    assert names == {"read_file", "lsp_hover"}


def test_filtered_get_returns_none_for_disallowed_tools(populated_registry: ToolRegistry) -> None:
    child = populated_registry.filtered({"read_file"})
    assert child.get("read_file") is not None
    assert child.get("write_file") is None
    assert child.get("bash") is None


def test_filtered_with_empty_set_returns_unrestricted_copy(populated_registry: ToolRegistry) -> None:
    """Empty set is the sentinel for 'no whitelist' (matches build role semantics)."""
    child = populated_registry.filtered(set())
    assert {t.name for t in child.all_tools()} == {
        "read_file", "write_file", "bash", "lsp_hover",
    }


def test_filtered_does_not_mutate_parent(populated_registry: ToolRegistry) -> None:
    parent_names_before = {t.name for t in populated_registry.all_tools()}
    populated_registry.filtered({"read_file"})
    parent_names_after = {t.name for t in populated_registry.all_tools()}
    assert parent_names_before == parent_names_after


def test_filtered_silently_drops_unknown_names(populated_registry: ToolRegistry) -> None:
    child = populated_registry.filtered({"read_file", "nonexistent_tool"})
    names = {t.name for t in child.all_tools()}
    assert names == {"read_file"}


def test_filtered_definitions_match_filter(populated_registry: ToolRegistry) -> None:
    child = populated_registry.filtered({"bash", "lsp_hover"})
    defs = child.definitions()
    assert {d.name for d in defs} == {"bash", "lsp_hover"}
