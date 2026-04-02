"""Tests for llm_code.tools.parsing — TDD."""
from __future__ import annotations

import pytest

from llm_code.tools.parsing import ParsedToolCall, parse_tool_calls


# ---------------------------------------------------------------------------
# ParsedToolCall dataclass
# ---------------------------------------------------------------------------

class TestParsedToolCall:
    def test_constructable(self):
        tc = ParsedToolCall(id="t1", name="bash", args={"command": "ls"}, source="native")
        assert tc.id == "t1"
        assert tc.name == "bash"
        assert tc.args == {"command": "ls"}
        assert tc.source == "native"

    def test_frozen(self):
        import dataclasses
        tc = ParsedToolCall(id="t1", name="bash", args={}, source="native")
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            tc.name = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Native tool call parsing
# ---------------------------------------------------------------------------

class TestNativeParsing:
    def test_single_native_call(self):
        native = [{"id": "t1", "name": "bash", "input": {"command": "ls"}}]
        result = parse_tool_calls("", native)
        assert len(result) == 1
        assert result[0].id == "t1"
        assert result[0].name == "bash"
        assert result[0].args == {"command": "ls"}
        assert result[0].source == "native"

    def test_multiple_native_calls(self):
        native = [
            {"id": "t1", "name": "bash", "input": {"command": "ls"}},
            {"id": "t2", "name": "read_file", "input": {"path": "/tmp/x"}},
        ]
        result = parse_tool_calls("", native)
        assert len(result) == 2
        assert result[0].name == "bash"
        assert result[1].name == "read_file"

    def test_native_takes_precedence_over_xml(self):
        text = '<tool_call>{"tool": "glob_search", "args": {"pattern": "*.py"}}</tool_call>'
        native = [{"id": "t1", "name": "bash", "input": {"command": "echo hi"}}]
        result = parse_tool_calls(text, native)
        assert len(result) == 1
        assert result[0].name == "bash"
        assert result[0].source == "native"

    def test_empty_native_list_falls_back_to_xml(self):
        text = '<tool_call>{"tool": "bash", "args": {"command": "ls"}}</tool_call>'
        result = parse_tool_calls(text, [])
        assert len(result) == 1
        assert result[0].source == "xml_tag"

    def test_none_native_falls_back_to_xml(self):
        text = '<tool_call>{"tool": "bash", "args": {"command": "ls"}}</tool_call>'
        result = parse_tool_calls(text, None)
        assert len(result) == 1
        assert result[0].source == "xml_tag"

    def test_native_ids_preserved(self):
        native = [{"id": "call_abc123", "name": "write_file", "input": {"path": "/x", "content": "hi"}}]
        result = parse_tool_calls("", native)
        assert result[0].id == "call_abc123"


# ---------------------------------------------------------------------------
# XML tag parsing
# ---------------------------------------------------------------------------

class TestXmlTagParsing:
    def test_single_xml_call(self):
        text = '<tool_call>{"tool": "bash", "args": {"command": "ls"}}</tool_call>'
        result = parse_tool_calls(text, None)
        assert len(result) == 1
        assert result[0].name == "bash"
        assert result[0].args == {"command": "ls"}
        assert result[0].source == "xml_tag"

    def test_multiple_xml_calls(self):
        text = (
            '<tool_call>{"tool": "bash", "args": {"command": "ls"}}</tool_call>\n'
            'some text\n'
            '<tool_call>{"tool": "read_file", "args": {"path": "/tmp/x"}}</tool_call>'
        )
        result = parse_tool_calls(text, None)
        assert len(result) == 2
        assert result[0].name == "bash"
        assert result[1].name == "read_file"

    def test_xml_call_has_generated_id(self):
        text = '<tool_call>{"tool": "bash", "args": {"command": "ls"}}</tool_call>'
        result = parse_tool_calls(text, None)
        assert result[0].id != ""
        assert result[0].id is not None

    def test_xml_source_field(self):
        text = '<tool_call>{"tool": "bash", "args": {}}</tool_call>'
        result = parse_tool_calls(text, None)
        assert result[0].source == "xml_tag"

    def test_malformed_json_skipped(self):
        text = (
            '<tool_call>NOT JSON</tool_call>\n'
            '<tool_call>{"tool": "bash", "args": {"command": "ls"}}</tool_call>'
        )
        result = parse_tool_calls(text, None)
        assert len(result) == 1
        assert result[0].name == "bash"

    def test_missing_tool_key_skipped(self):
        text = '<tool_call>{"args": {"command": "ls"}}</tool_call>'
        result = parse_tool_calls(text, None)
        assert len(result) == 0

    def test_empty_text_no_xml(self):
        result = parse_tool_calls("", None)
        assert result == []

    def test_text_with_no_tool_calls(self):
        result = parse_tool_calls("just some text with no tool calls", None)
        assert result == []

    def test_args_defaults_to_empty_dict_when_missing(self):
        text = '<tool_call>{"tool": "bash"}</tool_call>'
        result = parse_tool_calls(text, None)
        assert len(result) == 1
        assert result[0].args == {}

    def test_xml_ids_are_unique(self):
        text = (
            '<tool_call>{"tool": "bash", "args": {}}</tool_call>'
            '<tool_call>{"tool": "bash", "args": {}}</tool_call>'
        )
        result = parse_tool_calls(text, None)
        assert len(result) == 2
        assert result[0].id != result[1].id
