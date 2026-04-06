"""Tests for model parameter in SwarmCreateTool / SwarmCreateInput."""
from __future__ import annotations


from llm_code.tools.swarm_create import SwarmCreateInput, SwarmCreateTool


class TestSwarmCreateInputModelField:
    def test_input_has_model_field(self):
        """SwarmCreateInput accepts a model field."""
        inp = SwarmCreateInput(role="tester", task="run tests", model="qwen-fast")
        assert inp.model == "qwen-fast"

    def test_model_default_is_none(self):
        """model defaults to None when not provided."""
        inp = SwarmCreateInput(role="tester", task="run tests")
        assert inp.model is None

    def test_model_none_explicit(self):
        """model=None is accepted explicitly."""
        inp = SwarmCreateInput(role="tester", task="run tests", model=None)
        assert inp.model is None


class TestSwarmCreateToolSchema:
    def _make_tool(self):
        """Build SwarmCreateTool with a minimal mock manager."""
        from unittest.mock import MagicMock
        manager = MagicMock()
        return SwarmCreateTool(manager=manager)

    def test_schema_has_model_property(self):
        """input_schema includes a 'model' property."""
        tool = self._make_tool()
        schema = tool.input_schema
        assert "model" in schema["properties"]

    def test_model_schema_type_is_string(self):
        """The model property schema declares type string."""
        tool = self._make_tool()
        model_schema = tool.input_schema["properties"]["model"]
        assert model_schema.get("type") == "string"

    def test_model_not_required(self):
        """model is not in the required list."""
        tool = self._make_tool()
        required = tool.input_schema.get("required", [])
        assert "model" not in required
