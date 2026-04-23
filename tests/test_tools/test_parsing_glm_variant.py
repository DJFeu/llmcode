"""Tests for variant 6 — GLM-5.1 ``<tool_call>NAME}{JSON}</arg_value>``.

Observed in the wild on 2026-04-24 running glm-5.1 via llama.cpp
``--jinja``. The chat template emits tool calls in a non-standard
shape that neither the primary ``<tool_call>…</tool_call>`` regex
nor the bare ``<NAME>JSON</NAME>`` variant captures:

    <tool_call>web_search}{"query":"新聞","max_results":5}</arg_value>

Multiple calls separated by U+2192 ``→``.
"""
from __future__ import annotations

from llm_code.tools.parsing import parse_tool_calls


class TestGLMVariant:
    def test_single_tool_call(self) -> None:
        text = (
            '<tool_call>web_search}{"query":"今日熱門新聞",'
            '"max_results":5}</arg_value>'
        )
        calls = parse_tool_calls(text, native_tool_calls=None)
        assert len(calls) == 1
        assert calls[0].name == "web_search"
        assert calls[0].args == {"query": "今日熱門新聞", "max_results": 5}
        assert calls[0].source == "xml_tag"

    def test_multi_call_arrow_separated(self) -> None:
        """GLM chains multiple tool calls with U+2192 ``→``."""
        text = (
            '<tool_call>web_search}{"query":"今日熱門新聞 2026年4月","max_results":5}</arg_value>'
            "→"
            '<tool_call>web_search}{"query":"top news today April 2026","max_results":5}</arg_value>'
            "→"
            '<tool_call>web_search}{"query":"breaking news today","max_results":5}</arg_value>'
        )
        calls = parse_tool_calls(text, native_tool_calls=None)
        assert len(calls) == 3
        assert [c.name for c in calls] == ["web_search"] * 3
        queries = [c.args["query"] for c in calls]
        assert "今日熱門新聞 2026年4月" in queries
        assert "top news today April 2026" in queries
        assert "breaking news today" in queries

    def test_standard_format_still_wins(self) -> None:
        """If the response contains a proper ``<tool_call>…</tool_call>``
        block in llm-code's JSON-payload shape, the standard parser
        must match it (variant 6 is a fallback only)."""
        text = (
            '<tool_call>\n{"tool":"read_file","args":{"path":"/a"}}\n</tool_call>'
        )
        calls = parse_tool_calls(text, native_tool_calls=None)
        assert len(calls) == 1
        assert calls[0].name == "read_file"
        assert calls[0].args == {"path": "/a"}

    def test_bad_json_body_skipped(self) -> None:
        text = '<tool_call>web_search}{not valid json}</arg_value>'
        calls = parse_tool_calls(text, native_tool_calls=None)
        assert calls == []

    def test_non_dict_json_skipped(self) -> None:
        """A JSON list / number / string body doesn't qualify — tool
        args must be an object."""
        text = '<tool_call>web_search}[1,2,3]</arg_value>'
        calls = parse_tool_calls(text, native_tool_calls=None)
        assert calls == []

    def test_reserved_name_not_matched(self) -> None:
        """``<tool_call>tool_call}{…}</arg_value>`` would be meta-
        confusing; reserved names are rejected."""
        text = '<tool_call>tool_call}{"a":1}</arg_value>'
        calls = parse_tool_calls(text, native_tool_calls=None)
        assert calls == []

    def test_mixed_with_leading_text(self) -> None:
        """Reasoning text ahead of the tool call doesn't prevent the
        parse from picking up the call."""
        text = (
            "The user is asking for news. I should call web_search.\n\n"
            '<tool_call>web_search}{"query":"news","max_results":3}</arg_value>'
        )
        calls = parse_tool_calls(text, native_tool_calls=None)
        assert len(calls) == 1
        assert calls[0].args == {"query": "news", "max_results": 3}

    def test_nested_json_object_arg(self) -> None:
        """Args containing a nested object must still parse."""
        text = (
            '<tool_call>bash}{"command":"ls","options":{"flags":["-la"]}}</arg_value>'
        )
        calls = parse_tool_calls(text, native_tool_calls=None)
        assert len(calls) == 1
        assert calls[0].args["options"]["flags"] == ["-la"]
