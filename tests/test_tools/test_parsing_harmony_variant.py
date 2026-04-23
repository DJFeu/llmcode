"""Tests for variant 7 — Harmony/GLM ``<arg_key>/<arg_value>`` pairs.

Captured 2026-04-24 on glm-5.1: mid-session the model switched from
variant 6 ``NAME}{JSON}</arg_value>`` to the Harmony key-value pair
body within a standard ``<tool_call>…</tool_call>`` wrapper:

    <tool_call>
    web_search
    <arg_key>query</arg_key>
    <arg_value>今日熱門新聞</arg_value>
    <arg_key>max_results</arg_key>
    <arg_value>5</arg_value>
    </tool_call>
"""
from __future__ import annotations

from llm_code.tools.parsing import parse_tool_calls


class TestHarmonyVariant:
    def test_single_arg_string(self) -> None:
        text = (
            "<tool_call>\n"
            "web_search\n"
            "<arg_key>query</arg_key>\n"
            "<arg_value>今日熱門新聞</arg_value>\n"
            "</tool_call>"
        )
        calls = parse_tool_calls(text, native_tool_calls=None)
        assert len(calls) == 1
        assert calls[0].name == "web_search"
        assert calls[0].args == {"query": "今日熱門新聞"}

    def test_multi_args_including_numeric(self) -> None:
        """Numeric / boolean string values that round-trip as JSON
        should decode to their native type so the runtime receives
        ``max_results=5`` not ``max_results="5"``."""
        text = (
            "<tool_call>\n"
            "web_search\n"
            "<arg_key>query</arg_key>\n"
            "<arg_value>news</arg_value>\n"
            "<arg_key>max_results</arg_key>\n"
            "<arg_value>5</arg_value>\n"
            "<arg_key>include_images</arg_key>\n"
            "<arg_value>true</arg_value>\n"
            "</tool_call>"
        )
        calls = parse_tool_calls(text, native_tool_calls=None)
        assert len(calls) == 1
        assert calls[0].args["query"] == "news"
        assert calls[0].args["max_results"] == 5
        assert calls[0].args["include_images"] is True

    def test_json_object_value_decoded(self) -> None:
        text = (
            "<tool_call>\n"
            "bash\n"
            "<arg_key>options</arg_key>\n"
            '<arg_value>{"flag": "-la"}</arg_value>\n'
            "</tool_call>"
        )
        calls = parse_tool_calls(text, native_tool_calls=None)
        assert len(calls) == 1
        assert calls[0].args["options"] == {"flag": "-la"}

    def test_inline_single_line(self) -> None:
        """The model sometimes emits the whole variant 7 block on a
        single line — no newlines between the name and the first
        pair."""
        text = (
            "<tool_call>web_search<arg_key>query</arg_key>"
            "<arg_value>news</arg_value></tool_call>"
        )
        calls = parse_tool_calls(text, native_tool_calls=None)
        assert len(calls) == 1
        assert calls[0].args == {"query": "news"}

    def test_value_containing_angle_brackets_preserved(self) -> None:
        """The value body is captured non-greedily up to the next
        ``</arg_value>`` — a ``<`` inside the value must survive."""
        text = (
            "<tool_call>\n"
            "write_file\n"
            "<arg_key>path</arg_key>\n"
            "<arg_value>x.html</arg_value>\n"
            "<arg_key>content</arg_key>\n"
            "<arg_value><div>hi</div></arg_value>\n"
            "</tool_call>"
        )
        calls = parse_tool_calls(text, native_tool_calls=None)
        assert len(calls) == 1
        assert calls[0].args["content"] == "<div>hi</div>"

    def test_no_pairs_falls_through(self) -> None:
        """A ``<tool_call>`` with just a name but no
        ``<arg_key>/<arg_value>`` pairs is not variant 7 — parser
        returns None so other variants get their chance."""
        text = "<tool_call>\nweb_search\n</tool_call>"
        calls = parse_tool_calls(text, native_tool_calls=None)
        # No args at all — neither json-payload / hermes / variant 6
        # / variant 7 has anything to match. Empty result is fine.
        assert calls == []

    def test_reserved_name_rejected(self) -> None:
        text = (
            "<tool_call>\n"
            "tool_call\n"
            "<arg_key>x</arg_key>\n"
            "<arg_value>1</arg_value>\n"
            "</tool_call>"
        )
        calls = parse_tool_calls(text, native_tool_calls=None)
        assert calls == []

    def test_standard_json_payload_still_wins(self) -> None:
        """Well-formed JSON payload body must still take priority so
        we don't regress earlier models."""
        text = (
            '<tool_call>\n'
            '{"tool":"read_file","args":{"path":"/x"}}\n'
            '</tool_call>'
        )
        calls = parse_tool_calls(text, native_tool_calls=None)
        assert len(calls) == 1
        assert calls[0].name == "read_file"
        assert calls[0].args == {"path": "/x"}
