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


# ---------------------------------------------------------------------------
# Hermes / Qwen3 function-calling format
#
# vLLM-served Qwen3 (and many other tool-fine-tuned local models) emit
# tool calls inside <tool_call> using the Hermes function-calling syntax,
# NOT the JSON-payload format the original parser supported.
# ---------------------------------------------------------------------------

class TestHermesFormatParsing:
    def test_single_hermes_call_with_one_param(self):
        text = (
            "<tool_call>\n"
            "<function=bash>\n"
            "<parameter=command>\n"
            "ls -la\n"
            "</parameter>\n"
            "</function>\n"
            "</tool_call>"
        )
        result = parse_tool_calls(text, None)
        assert len(result) == 1
        assert result[0].name == "bash"
        assert result[0].args == {"command": "ls -la"}
        assert result[0].source == "xml_tag"

    def test_hermes_call_with_multiple_params(self):
        text = (
            "<tool_call>\n"
            "<function=web_search>\n"
            "<parameter=query>\n"
            "今日熱門新聞\n"
            "</parameter>\n"
            "<parameter=max_results>\n"
            "5\n"
            "</parameter>\n"
            "</function>\n"
            "</tool_call>"
        )
        result = parse_tool_calls(text, None)
        assert len(result) == 1
        assert result[0].name == "web_search"
        # Numeric strings stay as strings; the tool's pydantic input model
        # will coerce them. Same as native JSON would do.
        assert result[0].args["query"] == "今日熱門新聞"
        assert result[0].args["max_results"] in ("5", 5)

    def test_hermes_call_with_no_params(self):
        text = (
            "<tool_call>\n"
            "<function=git_status>\n"
            "</function>\n"
            "</tool_call>"
        )
        result = parse_tool_calls(text, None)
        assert len(result) == 1
        assert result[0].name == "git_status"
        assert result[0].args == {}

    def test_hermes_param_value_preserves_internal_whitespace(self):
        text = (
            "<tool_call>\n"
            "<function=write_file>\n"
            "<parameter=path>\n"
            "/tmp/x.py\n"
            "</parameter>\n"
            "<parameter=content>\n"
            "def foo():\n    return 42\n"
            "</parameter>\n"
            "</function>\n"
            "</tool_call>"
        )
        result = parse_tool_calls(text, None)
        assert len(result) == 1
        assert result[0].args["path"] == "/tmp/x.py"
        assert "def foo():" in result[0].args["content"]
        assert "return 42" in result[0].args["content"]

    def test_hermes_and_json_mixed_in_same_response(self):
        """Defensive: a single response should not interleave both formats,
        but if it does, both should parse."""
        text = (
            '<tool_call>{"tool": "bash", "args": {"command": "echo a"}}</tool_call>'
            "\n"
            "<tool_call>\n<function=bash>\n<parameter=command>\necho b\n</parameter>\n</function>\n</tool_call>"
        )
        result = parse_tool_calls(text, None)
        assert len(result) == 2
        commands = sorted(r.args["command"] for r in result)
        assert commands == ["echo a", "echo b"]

    def test_hermes_unknown_function_name_attribute_skipped(self):
        """Malformed Hermes block (no function= attr) should be skipped, not crash."""
        text = (
            "<tool_call>\n"
            "<function>\n"
            "<parameter=query>foo</parameter>\n"
            "</function>\n"
            "</tool_call>"
        )
        result = parse_tool_calls(text, None)
        assert result == []

    def test_hermes_multiple_calls_in_one_response(self):
        text = (
            "<tool_call>\n<function=glob_search>\n<parameter=pattern>\n*.py\n</parameter>\n</function>\n</tool_call>\n"
            "<tool_call>\n<function=read_file>\n<parameter=file_path>\n/tmp/x.py\n</parameter>\n</function>\n</tool_call>"
        )
        result = parse_tool_calls(text, None)
        assert len(result) == 2
        assert result[0].name == "glob_search"
        assert result[1].name == "read_file"


class TestHermesTemplateTruncatedFormat:
    """vLLM-served Qwen3 chat template injects ``<tool_call>\\n<function=``
    as the assistant prompt prefix in tool-calling mode. The model then
    continues with ``NAME>...params...</function></tool_call>``. The
    streamed response therefore contains ``<function=`` *missing* — the
    body of ``<tool_call>`` starts directly with the function name.

    Captured live from Qwen3.5-122B-A10B-int4-AutoRound on 2026-04-08.
    """

    def test_template_truncated_single_param(self):
        text = (
            "<tool_call>web_search>\n"
            "<parameter=query>\n"
            "今日熱門新聞\n"
            "</parameter>\n"
            "</function></tool_call>"
        )
        result = parse_tool_calls(text, None)
        assert len(result) == 1
        assert result[0].name == "web_search"
        assert result[0].args == {"query": "今日熱門新聞"}

    def test_template_truncated_exact_capture_from_production(self):
        """Verbatim bytes from /tmp/llm_code_parse_debug.log captured
        2026-04-08 from local Qwen3.5-122B."""
        text = (
            "<tool_call>web_search>\n"
            "<parameter=max_results>\n"
            "3</parameter>\n"
            "<parameter=query>\n"
            "今日熱門新聞\n"
            "</parameter>\n"
            "</function></tool_call>"
        )
        result = parse_tool_calls(text, None)
        assert len(result) == 1
        assert result[0].name == "web_search"
        assert result[0].args["query"] == "今日熱門新聞"
        assert result[0].args["max_results"] in ("3", 3)

    def test_template_truncated_no_params(self):
        text = "<tool_call>git_status>\n</function></tool_call>"
        result = parse_tool_calls(text, None)
        assert len(result) == 1
        assert result[0].name == "git_status"
        assert result[0].args == {}

    def test_template_truncated_does_not_clobber_full_form(self):
        """Both forms must coexist — full form still parses normally."""
        full = (
            "<tool_call>\n<function=read_file>\n"
            "<parameter=file_path>\n/tmp/x</parameter>\n"
            "</function>\n</tool_call>"
        )
        result = parse_tool_calls(full, None)
        assert len(result) == 1
        assert result[0].name == "read_file"
        assert result[0].args == {"file_path": "/tmp/x"}

    def test_template_truncated_with_underscore_in_name(self):
        text = (
            "<tool_call>my_special_tool>\n"
            "<parameter=arg>val</parameter>\n"
            "</function></tool_call>"
        )
        result = parse_tool_calls(text, None)
        assert len(result) == 1
        assert result[0].name == "my_special_tool"

    def test_template_truncated_function_literal_still_skipped(self):
        """Backward-compat: a literal '<function>' (no name, no equals)
        must NOT be parsed as a tool with name '<function>'. The
        truncated-format heuristic only fires when the body starts with a
        bare identifier followed by '>'."""
        text = (
            "<tool_call>\n"
            "<function>\n"
            "<parameter=query>foo</parameter>\n"
            "</function>\n"
            "</tool_call>"
        )
        result = parse_tool_calls(text, None)
        assert result == []
