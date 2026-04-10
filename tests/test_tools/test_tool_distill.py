"""Tests for tool description distillation."""
from __future__ import annotations

from llm_code.api.types import ToolDefinition
from llm_code.tools.tool_distill import (
    COMPACT_DESCRIPTIONS,
    distill_definitions,
)


def _make_def(name: str, desc: str = "Full description here") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=desc,
        input_schema={"type": "object", "properties": {}},
    )


class TestDistillDefinitions:
    def test_compact_false_unchanged(self) -> None:
        defs = (_make_def("read_file", "Read a file from disk"),)
        result = distill_definitions(defs, compact=False)
        assert result[0].description == "Read a file from disk"

    def test_compact_true_shortens(self) -> None:
        defs = (_make_def("read_file", "Read a file from the local filesystem. You can access any file..."),)
        result = distill_definitions(defs, compact=True)
        assert result[0].description == COMPACT_DESCRIPTIONS["read_file"]
        assert len(result[0].description) < 50

    def test_schema_preserved(self) -> None:
        schema = {"type": "object", "properties": {"path": {"type": "string"}}}
        defs = (ToolDefinition(name="read_file", description="long desc", input_schema=schema),)
        result = distill_definitions(defs, compact=True)
        assert result[0].input_schema == schema

    def test_unknown_tool_keeps_original(self) -> None:
        defs = (_make_def("custom_tool_xyz", "My custom tool"),)
        result = distill_definitions(defs, compact=True)
        assert result[0].description == "My custom tool"

    def test_does_not_mutate_original(self) -> None:
        original = _make_def("bash", "Run a shell command with full description")
        defs = (original,)
        distill_definitions(defs, compact=True)
        assert original.description == "Run a shell command with full description"

    def test_multiple_tools(self) -> None:
        defs = (
            _make_def("read_file", "long"),
            _make_def("bash", "long"),
            _make_def("unknown", "keep this"),
        )
        result = distill_definitions(defs, compact=True)
        assert result[0].description == COMPACT_DESCRIPTIONS["read_file"]
        assert result[1].description == COMPACT_DESCRIPTIONS["bash"]
        assert result[2].description == "keep this"

    def test_empty_input(self) -> None:
        assert distill_definitions((), compact=True) == ()

    def test_all_known_tools_have_compact(self) -> None:
        """Every tool in COMPACT_DESCRIPTIONS is a real short string."""
        for name, desc in COMPACT_DESCRIPTIONS.items():
            assert len(desc) < 60, f"{name}: '{desc}' too long for compact"
            assert len(desc) > 3, f"{name}: '{desc}' too short"
