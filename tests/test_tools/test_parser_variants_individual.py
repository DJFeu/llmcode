"""Per-variant match/parse unit tests.

Each of the 6 built-in parser variants gets exhaustive match + parse
coverage here. Existing higher-level tests in ``test_parsing.py``
exercise the integration path through ``parse_tool_calls``; these
tests hit the variant's ``match`` / ``parse`` hooks directly so a
regression surfaces on the exact variant owning the bug.
"""
from __future__ import annotations

from llm_code.tools.parser_variants import (
    bare_name_tag_variant,
    get_variant,
    glm_brace_variant,
    harmony_kv_variant,
    hermes_function_variant,
    hermes_truncated_variant,
    json_payload_variant,
)


# ---------------------------------------------------------------------------
# json_payload
# ---------------------------------------------------------------------------


class TestJsonPayloadVariant:
    v = json_payload_variant

    def test_match_object_with_tool_key(self) -> None:
        assert self.v.match('{"tool": "bash", "args": {}}') is True

    def test_match_object_without_tool_key_is_false(self) -> None:
        """Match is a cheap peek — it checks for ``"tool"`` substring.
        parse is what actually rejects no-tool bodies."""
        assert self.v.match('{"foo": "bar"}') is False

    def test_match_rejects_leading_non_brace(self) -> None:
        assert self.v.match('"tool": "bash"') is False

    def test_match_handles_leading_whitespace(self) -> None:
        assert self.v.match('   {"tool": "bash"}') is True

    def test_parse_flat_json(self) -> None:
        result = self.v.parse('{"tool": "bash", "args": {"command": "ls"}}')
        assert result is not None
        assert result.name == "bash"
        assert result.args == {"command": "ls"}
        assert result.source == "xml_tag"

    def test_parse_missing_tool_key_returns_none(self) -> None:
        assert self.v.parse('{"args": {"x": 1}}') is None

    def test_parse_malformed_json_returns_none(self) -> None:
        assert self.v.parse('{not json}') is None

    def test_parse_non_dict_returns_none(self) -> None:
        assert self.v.parse('["tool", "bash"]') is None

    def test_parse_no_args_defaults_to_empty_dict(self) -> None:
        result = self.v.parse('{"tool": "git_status"}')
        assert result is not None
        assert result.args == {}


# ---------------------------------------------------------------------------
# hermes_function
# ---------------------------------------------------------------------------


class TestHermesFunctionVariant:
    v = hermes_function_variant

    def test_match_positive(self) -> None:
        assert self.v.match("<function=bash>ls</function>") is True

    def test_match_negative_no_function_tag(self) -> None:
        assert self.v.match('{"tool": "bash"}') is False

    def test_parse_full_form(self) -> None:
        raw = "<function=bash>\n<parameter=command>\nls -la\n</parameter>\n</function>"
        result = self.v.parse(raw)
        assert result is not None
        assert result.name == "bash"
        assert result.args == {"command": "ls -la"}

    def test_parse_no_params_returns_empty_args(self) -> None:
        raw = "<function=git_status>\n</function>"
        result = self.v.parse(raw)
        assert result is not None
        assert result.name == "git_status"
        assert result.args == {}

    def test_parse_multiple_params(self) -> None:
        raw = (
            "<function=web_search>"
            "<parameter=query>news</parameter>"
            "<parameter=max_results>5</parameter>"
            "</function>"
        )
        result = self.v.parse(raw)
        assert result is not None
        assert result.args["query"] == "news"
        # Hermes parameter bodies stay as strings (runtime coerces).
        assert result.args["max_results"] in ("5", 5)

    def test_parse_nested_json_body(self) -> None:
        """Hermes also accepts JSON args at the top level inside
        ``<function=NAME>``."""
        raw = '<function=bash>{"command": "ls"}</function>'
        result = self.v.parse(raw)
        assert result is not None
        assert result.args == {"command": "ls"}


# ---------------------------------------------------------------------------
# hermes_truncated
# ---------------------------------------------------------------------------


class TestHermesTruncatedVariant:
    v = hermes_truncated_variant

    def test_match_name_gt(self) -> None:
        assert self.v.match("web_search>") is True

    def test_match_name_brace(self) -> None:
        """Variant 4 — no separator between name and JSON args."""
        assert self.v.match('bash{"command":"ls"}') is True

    def test_match_full_function_form_is_false(self) -> None:
        """The full form (``<function=NAME>``) starts with ``<``, not
        with an identifier — truncated match is anchored at ^."""
        assert self.v.match("<function=bash>") is False

    def test_match_leading_whitespace(self) -> None:
        assert self.v.match("  web_search>") is True

    def test_parse_truncated_params(self) -> None:
        raw = "bash>\n<parameter=command>\nls\n</parameter>\n</function>"
        result = self.v.parse(raw)
        assert result is not None
        assert result.name == "bash"
        assert result.args == {"command": "ls"}

    def test_parse_truncated_json_args(self) -> None:
        raw = 'bash>{"args": {"command": "ls"}}'
        result = self.v.parse(raw)
        assert result is not None
        assert result.name == "bash"
        assert result.args == {"command": "ls"}

    def test_parse_no_separator_json(self) -> None:
        """Variant 4 — ``NAME{...}`` with no ``>``."""
        raw = 'web_search{"args": {"query": "x"}}'
        result = self.v.parse(raw)
        assert result is not None
        assert result.name == "web_search"
        assert result.args == {"query": "x"}


# ---------------------------------------------------------------------------
# harmony_kv (variant 7)
# ---------------------------------------------------------------------------


class TestHarmonyKvVariant:
    v = harmony_kv_variant

    def test_match_arg_key_substring(self) -> None:
        assert self.v.match("web_search\n<arg_key>q</arg_key>") is True

    def test_match_no_arg_key_is_false(self) -> None:
        assert self.v.match("web_search\nquery=x") is False

    def test_parse_single_pair(self) -> None:
        raw = (
            "web_search\n"
            "<arg_key>query</arg_key>\n"
            "<arg_value>news</arg_value>\n"
        )
        result = self.v.parse(raw)
        assert result is not None
        assert result.name == "web_search"
        assert result.args == {"query": "news"}

    def test_parse_multi_pair_with_types(self) -> None:
        raw = (
            "web_search\n"
            "<arg_key>query</arg_key><arg_value>news</arg_value>"
            "<arg_key>max_results</arg_key><arg_value>5</arg_value>"
            "<arg_key>include_images</arg_key><arg_value>true</arg_value>"
        )
        result = self.v.parse(raw)
        assert result is not None
        assert result.args["query"] == "news"
        assert result.args["max_results"] == 5
        assert result.args["include_images"] is True

    def test_parse_value_with_inner_angle_brackets(self) -> None:
        raw = (
            "write_file\n"
            "<arg_key>content</arg_key>"
            "<arg_value><div>hi</div></arg_value>"
        )
        result = self.v.parse(raw)
        assert result is not None
        assert result.args["content"] == "<div>hi</div>"

    def test_parse_reserved_name_returns_none(self) -> None:
        raw = "tool_call\n<arg_key>x</arg_key><arg_value>1</arg_value>"
        assert self.v.parse(raw) is None

    def test_parse_no_pairs_returns_none(self) -> None:
        raw = "web_search\n"
        assert self.v.parse(raw) is None

    def test_requires_standard_close_when_contract(self) -> None:
        """Contract with stream parser: when ``<arg_key>`` is in the
        buffer, the stream parser must NOT use custom close tags."""
        assert self.v.requires_standard_close_when == ("<arg_key>",)


# ---------------------------------------------------------------------------
# glm_brace (variant 6)
# ---------------------------------------------------------------------------


class TestGlmBraceVariant:
    v = glm_brace_variant

    def test_match_positive(self) -> None:
        assert self.v.match('web_search}{"query":"x"}') is True

    def test_match_with_leading_whitespace(self) -> None:
        assert self.v.match('  web_search}{"q":"x"}') is True

    def test_match_negative_no_brace(self) -> None:
        assert self.v.match('web_search{"q":"x"}') is False

    def test_match_negative_non_identifier_start(self) -> None:
        assert self.v.match('1bad_name}{"x":1}') is False

    def test_parse_single_call(self) -> None:
        raw = 'web_search}{"query":"news","max_results":5}'
        result = self.v.parse(raw)
        assert result is not None
        assert result.name == "web_search"
        assert result.args == {"query": "news", "max_results": 5}

    def test_parse_reserved_name_returns_none(self) -> None:
        raw = 'tool_call}{"x":1}'
        assert self.v.parse(raw) is None

    def test_parse_bad_json_returns_none(self) -> None:
        raw = 'web_search}{not json}'
        assert self.v.parse(raw) is None

    def test_parse_non_dict_json_returns_none(self) -> None:
        raw = 'web_search}[1,2,3]'
        assert self.v.parse(raw) is None

    def test_parse_with_pre_wrapped_body(self) -> None:
        """If a caller includes the ``<tool_call>`` wrapper in the
        raw body, the adapter still parses it (the regex matches
        the wrapped form natively)."""
        raw = '<tool_call>web_search}{"q":"x"}</arg_value>'
        result = self.v.parse(raw)
        assert result is not None
        assert result.name == "web_search"

    def test_parse_nested_object_arg(self) -> None:
        raw = 'bash}{"command":"ls","options":{"flags":["-la"]}}'
        result = self.v.parse(raw)
        assert result is not None
        assert result.args["options"]["flags"] == ["-la"]


# ---------------------------------------------------------------------------
# bare_name_tag (variant 5)
# ---------------------------------------------------------------------------


class TestBareNameTagVariant:
    v = bare_name_tag_variant

    def test_match_positive(self) -> None:
        assert self.v.match('<web_search>{"q":"x"}</web_search>') is True

    def test_match_mismatched_close(self) -> None:
        """Qwen3.5 sometimes emits mismatched closers."""
        assert self.v.match('<web_search>{"q":"x"}</other>') is True

    def test_match_negative_no_json(self) -> None:
        assert self.v.match("<web_search>not json</web_search>") is False

    def test_parse_flat_body(self) -> None:
        raw = '<web_search>{"query":"news"}</web_search>'
        result = self.v.parse(raw)
        assert result is not None
        assert result.name == "web_search"
        assert result.args == {"query": "news"}

    def test_parse_nested_args_key_unwrapped(self) -> None:
        raw = '<read_file>{"args": {"path": "foo.py"}}</read_file>'
        result = self.v.parse(raw)
        assert result is not None
        assert result.args == {"path": "foo.py"}

    def test_parse_nested_arguments_key_unwrapped(self) -> None:
        raw = '<run_cmd>{"arguments": {"cmd": "ls"}}</run_cmd>'
        result = self.v.parse(raw)
        assert result is not None
        assert result.args == {"cmd": "ls"}

    def test_parse_mismatched_close_accepted(self) -> None:
        raw = '<web_search>{"q":"x"}</search>'
        result = self.v.parse(raw)
        assert result is not None
        assert result.name == "web_search"

    def test_parse_reserved_name_returns_none(self) -> None:
        """Tag names in the reserved set (tool_call, think, etc.)
        must never be re-interpreted as tool calls."""
        raw = '<think>{"a": 1}</think>'
        assert self.v.parse(raw) is None

    def test_parse_scalar_body_returns_none(self) -> None:
        assert self.v.parse('<web_search>"just a string"</web_search>') is None

    def test_parse_list_body_returns_none(self) -> None:
        assert self.v.parse('<web_search>[1,2,3]</web_search>') is None


# ---------------------------------------------------------------------------
# Registry re-exports match the variant instances
# ---------------------------------------------------------------------------


class TestRegistryConsistency:
    def test_get_variant_returns_same_instance_as_module_export(self) -> None:
        assert get_variant("json_payload") is json_payload_variant
        assert get_variant("hermes_function") is hermes_function_variant
        assert get_variant("hermes_truncated") is hermes_truncated_variant
        assert get_variant("harmony_kv") is harmony_kv_variant
        assert get_variant("glm_brace") is glm_brace_variant
        assert get_variant("bare_name_tag") is bare_name_tag_variant

    def test_each_variant_has_unique_name(self) -> None:
        names = [
            json_payload_variant.name,
            hermes_function_variant.name,
            hermes_truncated_variant.name,
            harmony_kv_variant.name,
            glm_brace_variant.name,
            bare_name_tag_variant.name,
        ]
        assert len(set(names)) == len(names)

    def test_each_variant_has_callable_match(self) -> None:
        for v in (
            json_payload_variant,
            hermes_function_variant,
            hermes_truncated_variant,
            harmony_kv_variant,
            glm_brace_variant,
            bare_name_tag_variant,
        ):
            assert callable(v.match)
            # match on a neutral string must not crash
            assert isinstance(v.match(""), bool)

    def test_each_variant_has_callable_parse(self) -> None:
        for v in (
            json_payload_variant,
            hermes_function_variant,
            hermes_truncated_variant,
            harmony_kv_variant,
            glm_brace_variant,
            bare_name_tag_variant,
        ):
            assert callable(v.parse)
            # parse on a neutral string must not crash; may return None
            result = v.parse("")
            assert result is None or hasattr(result, "name")
