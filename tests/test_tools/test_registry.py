"""Tests for llm_code.tools.base and llm_code.tools.registry — TDD: written before implementation."""
from __future__ import annotations

import pytest

from llm_code.api.types import ToolDefinition
from llm_code.tools.base import PermissionLevel, Tool, ToolResult
from llm_code.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# ToolResult
# ---------------------------------------------------------------------------

class TestToolResult:
    def test_constructable_minimal(self):
        r = ToolResult(output="done")
        assert r.output == "done"
        assert r.is_error is False
        assert r.metadata is None

    def test_constructable_with_error(self):
        r = ToolResult(output="fail", is_error=True)
        assert r.is_error is True

    def test_constructable_with_metadata(self):
        r = ToolResult(output="img", metadata={"type": "image"})
        assert r.metadata == {"type": "image"}

    def test_frozen(self):
        import dataclasses
        r = ToolResult(output="x")
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            r.output = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# PermissionLevel
# ---------------------------------------------------------------------------

class TestPermissionLevel:
    def test_values(self):
        assert PermissionLevel.READ_ONLY.value == "read_only"
        assert PermissionLevel.WORKSPACE_WRITE.value == "workspace_write"
        assert PermissionLevel.FULL_ACCESS.value == "full_access"


# ---------------------------------------------------------------------------
# Concrete Tool stub for testing
# ---------------------------------------------------------------------------

class EchoTool(Tool):
    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echoes the input back"

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    def execute(self, args: dict) -> ToolResult:
        return ToolResult(output=args.get("message", ""))


class WriteTool(Tool):
    @property
    def name(self) -> str:
        return "write"

    @property
    def description(self) -> str:
        return "Writes something"

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.WORKSPACE_WRITE

    def execute(self, args: dict) -> ToolResult:
        return ToolResult(output="written")


# ---------------------------------------------------------------------------
# Tool.to_definition()
# ---------------------------------------------------------------------------

class TestToolToDefinition:
    def test_returns_tool_definition(self):
        tool = EchoTool()
        defn = tool.to_definition()
        assert isinstance(defn, ToolDefinition)

    def test_definition_has_correct_fields(self):
        tool = EchoTool()
        defn = tool.to_definition()
        assert defn.name == "echo"
        assert defn.description == "Echoes the input back"
        assert defn.input_schema == tool.input_schema

    def test_definition_is_frozen(self):
        import dataclasses
        tool = EchoTool()
        defn = tool.to_definition()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            defn.name = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------

class TestToolRegistry:
    def test_empty_registry(self):
        reg = ToolRegistry()
        assert reg.all_tools() == ()

    def test_register_and_get(self):
        reg = ToolRegistry()
        tool = EchoTool()
        reg.register(tool)
        assert reg.get("echo") is tool

    def test_get_missing_returns_none(self):
        reg = ToolRegistry()
        assert reg.get("nonexistent") is None

    def test_register_duplicate_raises(self):
        reg = ToolRegistry()
        reg.register(EchoTool())
        with pytest.raises(ValueError, match="echo"):
            reg.register(EchoTool())

    def test_all_tools_returns_tuple(self):
        reg = ToolRegistry()
        reg.register(EchoTool())
        reg.register(WriteTool())
        tools = reg.all_tools()
        assert isinstance(tools, tuple)
        assert len(tools) == 2

    def test_definitions_no_filter(self):
        reg = ToolRegistry()
        reg.register(EchoTool())
        reg.register(WriteTool())
        defs = reg.definitions()
        assert isinstance(defs, tuple)
        assert len(defs) == 2
        names = {d.name for d in defs}
        assert names == {"echo", "write"}

    def test_definitions_with_allowed_filter(self):
        reg = ToolRegistry()
        reg.register(EchoTool())
        reg.register(WriteTool())
        defs = reg.definitions(allowed={"echo"})
        assert len(defs) == 1
        assert defs[0].name == "echo"

    def test_definitions_allowed_empty_set(self):
        reg = ToolRegistry()
        reg.register(EchoTool())
        defs = reg.definitions(allowed=set())
        assert defs == ()

    def test_execute_success(self):
        reg = ToolRegistry()
        reg.register(EchoTool())
        result = reg.execute("echo", {"message": "hello"})
        assert result.output == "hello"
        assert result.is_error is False

    def test_execute_not_found_returns_error(self):
        reg = ToolRegistry()
        result = reg.execute("nonexistent", {})
        assert result.is_error is True
        assert "nonexistent" in result.output

    def test_definitions_allowed_none_returns_all(self):
        reg = ToolRegistry()
        reg.register(EchoTool())
        reg.register(WriteTool())
        defs = reg.definitions(allowed=None)
        assert len(defs) == 2
