"""Tests for Tool ABC input validation via Pydantic models."""
from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from llm_code.tools.base import PermissionLevel, Tool, ToolResult


class ReadArgs(BaseModel):
    path: str
    encoding: str = "utf-8"


class ToolWithModel(Tool):
    """Tool that declares a Pydantic input model."""

    @property
    def name(self) -> str:
        return "tool_with_model"

    @property
    def description(self) -> str:
        return "Tool with Pydantic validation"

    @property
    def input_schema(self) -> dict:
        return ReadArgs.model_json_schema()

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    @property
    def input_model(self) -> type[BaseModel]:
        return ReadArgs

    def execute(self, args: dict) -> ToolResult:
        return ToolResult(output=f"read {args['path']} with {args['encoding']}")


class ToolWithoutModel(Tool):
    """Tool that declares no Pydantic input model."""

    @property
    def name(self) -> str:
        return "tool_without_model"

    @property
    def description(self) -> str:
        return "Tool without Pydantic validation"

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    def execute(self, args: dict) -> ToolResult:
        return ToolResult(output="executed")


class TestInputModelDefault:
    def test_tool_without_model_returns_none(self) -> None:
        tool = ToolWithoutModel()
        assert tool.input_model is None


class TestValidateInput:
    def test_with_model_valid_args_returns_validated_dict(self) -> None:
        tool = ToolWithModel()
        result = tool.validate_input({"path": "/tmp/file.txt"})
        assert result == {"path": "/tmp/file.txt", "encoding": "utf-8"}

    def test_with_model_valid_args_with_encoding(self) -> None:
        tool = ToolWithModel()
        result = tool.validate_input({"path": "/tmp/file.txt", "encoding": "latin-1"})
        assert result == {"path": "/tmp/file.txt", "encoding": "latin-1"}

    def test_with_model_missing_required_field_raises_validation_error(self) -> None:
        tool = ToolWithModel()
        with pytest.raises(ValidationError):
            tool.validate_input({})

    def test_without_model_passes_args_through_unchanged(self) -> None:
        tool = ToolWithoutModel()
        args = {"anything": 42, "nested": {"key": "val"}}
        result = tool.validate_input(args)
        assert result == args

    def test_without_model_empty_dict_passes_through(self) -> None:
        tool = ToolWithoutModel()
        result = tool.validate_input({})
        assert result == {}

    def test_with_model_extra_fields_handled(self) -> None:
        tool = ToolWithModel()
        # Pydantic default behavior for extra fields (ignore by default)
        result = tool.validate_input({"path": "/tmp/f.txt", "unknown": "extra"})
        assert result["path"] == "/tmp/f.txt"
        assert result["encoding"] == "utf-8"
