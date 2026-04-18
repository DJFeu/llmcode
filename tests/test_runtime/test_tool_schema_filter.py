"""M8: tool schema 預篩 for XML-mode models."""
from __future__ import annotations


class TestXmlModeFilter:
    def test_has_xml_schema_filter_helper(self) -> None:
        from llm_code.runtime.tool_schema_filter import (
            filter_schemas_for_xml_mode,
        )
        assert callable(filter_schemas_for_xml_mode)

    def test_xml_mode_strips_complex_schemas(self) -> None:
        from llm_code.runtime.tool_schema_filter import (
            filter_schemas_for_xml_mode,
        )
        # Qwen OSS in XML mode gets a simplified tool surface —
        # prefer tools whose input schemas are flat dicts without
        # tuple / anyOf shapes.
        schemas = [
            {"name": "simple", "parameters": {"type": "object",
                                              "properties": {"x": {"type": "string"}}}},
            {"name": "complex", "parameters": {"type": "object",
                                               "anyOf": [{"x": "y"}]}},
        ]
        result = filter_schemas_for_xml_mode(schemas)
        names = [s["name"] for s in result]
        assert "simple" in names
        assert "complex" not in names

    def test_non_xml_mode_passthrough(self) -> None:
        from llm_code.runtime.tool_schema_filter import (
            filter_schemas_native,
        )
        schemas = [
            {"name": "simple", "parameters": {"type": "object"}},
            {"name": "complex", "parameters": {"anyOf": [{"x": 1}]}},
        ]
        # Native tools keep every schema — Qwen cloud & Claude can
        # handle the full surface.
        assert filter_schemas_native(schemas) == schemas
