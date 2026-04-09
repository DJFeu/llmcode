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


class TestHermesTruncatedJsonArgsFormat:
    """Yet another sub-format observed in production: vLLM-served Qwen3
    sometimes emits the bare function name followed by a JSON object
    containing the args, with NO <parameter=...> blocks and NO closing
    </function> tag.

    Captured live from Qwen3.5-122B on 2026-04-08:

        <tool_call>web_search>{"args": {"query": "今日熱門新聞", "max_results": 3}}</tool_call>

    The model is mixing the truncated function-name prefix with a
    JSON-style argument payload. The parser must detect that the body
    after NAME> is a JSON object and extract args from either the top-
    level dict or its 'args'/'arguments' key.
    """

    def test_truncated_with_json_args_wrapper(self):
        """Args nested under 'args' key — exact production capture."""
        text = (
            '<tool_call>web_search>{"args": {"query": "今日熱門新聞", "max_results": 3}}</tool_call>'
        )
        result = parse_tool_calls(text, None)
        assert len(result) == 1
        assert result[0].name == "web_search"
        assert result[0].args == {"query": "今日熱門新聞", "max_results": 3}

    def test_truncated_with_json_arguments_wrapper(self):
        """Some Hermes-finetuned models use 'arguments' instead of 'args'."""
        text = (
            '<tool_call>read_file>{"arguments": {"file_path": "/tmp/x"}}</tool_call>'
        )
        result = parse_tool_calls(text, None)
        assert len(result) == 1
        assert result[0].name == "read_file"
        assert result[0].args == {"file_path": "/tmp/x"}

    def test_truncated_with_flat_json_args(self):
        """Args directly at the top level of the JSON object — no wrapper."""
        text = (
            '<tool_call>bash>{"command": "ls -la"}</tool_call>'
        )
        result = parse_tool_calls(text, None)
        assert len(result) == 1
        assert result[0].name == "bash"
        assert result[0].args == {"command": "ls -la"}

    def test_truncated_with_json_args_multiline(self):
        text = (
            '<tool_call>web_search>\n'
            '{"args": {"query": "test", "max_results": 5}}\n'
            '</tool_call>'
        )
        result = parse_tool_calls(text, None)
        assert len(result) == 1
        assert result[0].name == "web_search"
        assert result[0].args == {"query": "test", "max_results": 5}

    def test_truncated_with_json_args_and_function_close(self):
        """Some emissions include both the JSON args AND a </function> close."""
        text = (
            '<tool_call>web_search>{"args": {"query": "x"}}</function></tool_call>'
        )
        result = parse_tool_calls(text, None)
        assert len(result) == 1
        assert result[0].name == "web_search"
        assert result[0].args == {"query": "x"}

    def test_truncated_invalid_json_after_name_falls_through(self):
        """If body after NAME> isn't valid JSON AND has no parameter blocks,
        return empty args (not None — the call still exists)."""
        text = (
            '<tool_call>git_status>not-json-not-params</tool_call>'
        )
        result = parse_tool_calls(text, None)
        # We still recognize the call name, args just empty
        assert len(result) == 1
        assert result[0].name == "git_status"
        assert result[0].args == {}


class TestHermesTruncatedNoSeparatorFormat:
    """Variant 4: Qwen3.5 sometimes omits the ``>`` separator entirely and
    emits ``<tool_call>NAME{"args": {...}}</tool_call>`` — function name
    directly followed by the JSON object, no delimiter.

    Captured live from Qwen3.5-122B on 2026-04-08."""

    def test_truncated_no_separator_json_args(self) -> None:
        text = (
            '<tool_call>web_search{"args": {"max_results": 5, "query": "今日熱門新聞 2026"}}</tool_call>'
        )
        result = parse_tool_calls(text, None)
        assert len(result) == 1
        assert result[0].name == "web_search"
        assert result[0].args == {"max_results": 5, "query": "今日熱門新聞 2026"}

    def test_truncated_no_separator_with_whitespace(self) -> None:
        """Whitespace between name and '{' should still parse."""
        text = '<tool_call>bash {"command": "ls"}</tool_call>'
        result = parse_tool_calls(text, None)
        assert len(result) == 1
        assert result[0].name == "bash"
        assert result[0].args == {"command": "ls"}

    def test_truncated_no_separator_with_newline(self) -> None:
        text = '<tool_call>read_file\n{"file_path": "/tmp/x"}\n</tool_call>'
        result = parse_tool_calls(text, None)
        assert len(result) == 1
        assert result[0].name == "read_file"
        assert result[0].args == {"file_path": "/tmp/x"}

    def test_truncated_no_separator_with_arguments_wrapper(self) -> None:
        text = '<tool_call>web_fetch{"arguments": {"url": "https://example.com"}}</tool_call>'
        result = parse_tool_calls(text, None)
        assert len(result) == 1
        assert result[0].name == "web_fetch"
        assert result[0].args == {"url": "https://example.com"}


class TestBareNameTagVariant:
    """Variant 5: bare ``<NAME>JSON</NAME>`` from Qwen3.5 vLLM chat
    templates that omit the ``<tool_call>`` wrapping entirely.
    Captured 2026-04-09 from a field report."""

    def test_bare_web_search_parsed(self):
        text = '<web_search>{"query": "今日熱門新聞", "max_results": 3}</web_search>'
        result = parse_tool_calls(text, None)
        assert len(result) == 1
        assert result[0].name == "web_search"
        assert result[0].args == {"query": "今日熱門新聞", "max_results": 3}

    def test_missing_leading_angle_still_matches(self):
        """Terminal rendering or prompt-prefix injection can drop the
        leading ``<`` — the regex has it optional for resilience."""
        text = 'web_search>{"query": "x"}</web_search>'
        result = parse_tool_calls(text, None)
        assert len(result) == 1
        assert result[0].name == "web_search"

    def test_bare_variant_in_mixed_prose(self):
        text = '根據查詢 <web_search>{"query": "x"}</web_search> 進行搜尋'
        result = parse_tool_calls(text, None)
        assert len(result) == 1
        assert result[0].name == "web_search"

    def test_bare_variant_multiline_body(self):
        text = '<web_search>\n{"query": "今日新聞", "max_results": 3}\n</web_search>'
        result = parse_tool_calls(text, None)
        assert len(result) == 1
        assert result[0].args == {"query": "今日新聞", "max_results": 3}

    def test_nested_args_key_unwrapped(self):
        text = '<read_file>{"args": {"path": "foo.py"}}</read_file>'
        result = parse_tool_calls(text, None)
        assert len(result) == 1
        assert result[0].args == {"path": "foo.py"}

    def test_nested_arguments_key_unwrapped(self):
        text = '<run_cmd>{"arguments": {"cmd": "ls"}}</run_cmd>'
        result = parse_tool_calls(text, None)
        assert len(result) == 1
        assert result[0].args == {"cmd": "ls"}

    def test_known_tool_names_filters_false_positive_p_tag(self):
        """Without the guard, ``<p>{"a":1}</p>`` would match the bare
        variant because 'p' is a valid identifier and the body is
        valid JSON. The ``known_tool_names`` set blocks it."""
        text = '<p>{"a": 1}</p>'
        # Permissive mode matches (documented caveat)
        assert len(parse_tool_calls(text, None)) == 1
        # Production mode with the registry set blocks it
        assert len(parse_tool_calls(
            text, None, known_tool_names={"web_search", "read_file"}
        )) == 0

    def test_invalid_json_rejected(self):
        text = '<web_search>{not valid json}</web_search>'
        assert parse_tool_calls(text, None) == []

    def test_mismatched_close_tag_rejected(self):
        text = '<web_search>{"q": "x"}</other>'
        assert parse_tool_calls(text, None) == []

    def test_scalar_json_rejected(self):
        """Only object bodies are valid tool args. A scalar / list
        body in the bare variant is treated as not-a-tool-call."""
        assert parse_tool_calls('<web_search>"string"</web_search>', None) == []
        assert parse_tool_calls('<web_search>[1,2,3]</web_search>', None) == []

    def test_reserved_tool_call_name_not_reinterpreted(self):
        """A malformed ``<tool_call>{"args": {}}</tool_call>`` (no
        "tool" key in JSON) used to return zero calls. After adding
        variant 5, this regex could double-match it as a tool named
        "tool_call" — the ``_VARIANT_5_RESERVED_NAMES`` guard prevents
        that. This is a regression guard for
        ``test_missing_tool_key_skipped``."""
        text = '<tool_call>{"args": {"command": "ls"}}</tool_call>'
        result = parse_tool_calls(text, None)
        assert len(result) == 0

    def test_reserved_think_name_not_reinterpreted(self):
        """A ``<think>{"a": 1}</think>`` is thinking content, not a
        tool call named 'think'. The reserved-names guard blocks it."""
        text = '<think>{"a": 1}</think>'
        assert parse_tool_calls(text, None) == []

    def test_variant5_only_fires_when_tool_call_wrapper_absent(self):
        """A well-formed ``<tool_call>`` block MUST go through the
        fast path and variant 5 must not double-parse the same
        content. Pinned so a future refactor doesn't introduce
        duplicate tool calls."""
        text = '<tool_call>{"tool": "web_search", "args": {"q": "x"}}</tool_call>'
        result = parse_tool_calls(text, None)
        assert len(result) == 1  # exactly one, not two
        assert result[0].name == "web_search"

    def test_multiple_bare_tool_calls_in_one_text(self):
        text = (
            '<web_search>{"query": "A"}</web_search>\n'
            '後續 <web_search>{"query": "B"}</web_search>'
        )
        result = parse_tool_calls(text, None)
        assert len(result) == 2
        assert result[0].args == {"query": "A"}
        assert result[1].args == {"query": "B"}
