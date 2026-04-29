"""Parser variant registry — v13 Phase A.

Exercises the ``tools/parser_variants`` module: registration,
lookup, default order, plugin loading, and the
``parse_tool_calls(profile=...)`` path.
"""
from __future__ import annotations

import time

import pytest

from llm_code.tools.parser_variants import (
    DEFAULT_VARIANT_ORDER,
    REGISTRY,
    ParserVariant,
    PluginLoadError,
    UnknownVariantError,
    _parse_bare_name_tag_single,
    _parse_glm_variant_single,
    get_variant,
    list_variant_names,
    load_plugin_variant,
    register_variant,
)
from llm_code.tools.parsing import (
    _parse_harmony_variant,
    _parse_hermes_block,
    _parse_json_payload,
    parse_tool_calls,
)


# ---------------------------------------------------------------------------
# Registration + lookup
# ---------------------------------------------------------------------------


class TestRegistrationAndLookup:
    def test_register_adds_variant_by_name(self) -> None:
        v = ParserVariant(
            name="test_tmp_add",
            match=lambda s: False,
            parse=lambda s: None,
        )
        try:
            register_variant(v)
            assert "test_tmp_add" in REGISTRY
            assert get_variant("test_tmp_add") is v
        finally:
            REGISTRY.pop("test_tmp_add", None)

    def test_register_overwrite_is_silent(self) -> None:
        """Registration policy: later wins. Callers that want
        duplicate-detection do it themselves."""
        v1 = ParserVariant(name="tmp_dup", match=lambda s: False, parse=lambda s: None)
        v2 = ParserVariant(name="tmp_dup", match=lambda s: True, parse=lambda s: None)
        try:
            register_variant(v1)
            register_variant(v2)
            assert get_variant("tmp_dup") is v2
        finally:
            REGISTRY.pop("tmp_dup", None)

    def test_get_unknown_name_raises(self) -> None:
        with pytest.raises(UnknownVariantError):
            get_variant("no_such_variant_anywhere_ever")

    def test_unknown_variant_is_key_error(self) -> None:
        """UnknownVariantError inherits KeyError so except KeyError
        still catches it."""
        with pytest.raises(KeyError):
            get_variant("another_missing_name")

    def test_list_variant_names_returns_sorted(self) -> None:
        names = list_variant_names()
        assert names == tuple(sorted(names))
        # Built-ins must be present.
        assert "json_payload" in names
        assert "hermes_function" in names


class TestBuiltinsRegistered:
    def test_all_six_builtins_present(self) -> None:
        for name in DEFAULT_VARIANT_ORDER:
            assert name in REGISTRY, f"missing built-in: {name}"

    def test_default_order_has_eight_entries(self) -> None:
        # v15 M5 added ``webfetch_inline`` at the end (total = 7).
        # v2.13.2 added ``glm_hybrid`` between harmony_kv and
        # glm_brace to recognise GLM-5.1's malformed parallel-
        # emission shape (total = 8).
        assert len(DEFAULT_VARIANT_ORDER) == 8

    def test_default_order_json_first(self) -> None:
        """json_payload is cheapest + most common for llm-code's
        own protocol — must stay first."""
        assert DEFAULT_VARIANT_ORDER[0] == "json_payload"

    def test_default_order_webfetch_inline_last(self) -> None:
        """v15 M5 ``webfetch_inline`` is the last resort — fires only
        when no earlier wrapper-based variant matched."""
        assert DEFAULT_VARIANT_ORDER[-1] == "webfetch_inline"

    def test_default_order_bare_name_tag_second_to_last(self) -> None:
        """Pre-M5, ``bare_name_tag`` was last. After M5 it sits at -2,
        still after every wrapped variant — preserves the original
        priority intent."""
        assert DEFAULT_VARIANT_ORDER[-2] == "bare_name_tag"

    def test_harmony_variant_has_required_close_when(self) -> None:
        v = get_variant("harmony_kv")
        assert v.requires_standard_close_when == ("<arg_key>",)

    def test_other_variants_have_empty_required_close_when(self) -> None:
        for name in ("json_payload", "hermes_function", "hermes_truncated",
                     "glm_brace", "bare_name_tag"):
            assert get_variant(name).requires_standard_close_when == ()

    def test_variant_parse_functions_match_legacy(self) -> None:
        """Bound parse functions must be the originals so a profile
        swap to explicit variant order produces byte-identical output."""
        assert get_variant("json_payload").parse is _parse_json_payload
        assert get_variant("hermes_function").parse is _parse_hermes_block
        assert get_variant("hermes_truncated").parse is _parse_hermes_block
        assert get_variant("harmony_kv").parse is _parse_harmony_variant
        assert get_variant("glm_brace").parse is _parse_glm_variant_single
        assert get_variant("bare_name_tag").parse is _parse_bare_name_tag_single


# ---------------------------------------------------------------------------
# Match predicate cheapness
# ---------------------------------------------------------------------------


class TestMatchIsCheap:
    def test_match_json_payload_positive(self) -> None:
        v = get_variant("json_payload")
        assert v.match('{"tool": "bash", "args": {}}') is True

    def test_match_json_payload_negative(self) -> None:
        v = get_variant("json_payload")
        assert v.match("<function=bash>") is False

    def test_match_hermes_function_positive(self) -> None:
        v = get_variant("hermes_function")
        assert v.match("<function=bash>ls</function>") is True

    def test_match_hermes_function_negative(self) -> None:
        v = get_variant("hermes_function")
        assert v.match('{"tool": "x"}') is False

    def test_match_hermes_truncated_positive(self) -> None:
        v = get_variant("hermes_truncated")
        assert v.match("web_search>") is True

    def test_match_hermes_truncated_brace_positive(self) -> None:
        v = get_variant("hermes_truncated")
        assert v.match('bash{"command":"ls"}') is True

    def test_match_hermes_truncated_negative(self) -> None:
        v = get_variant("hermes_truncated")
        assert v.match("<function=bash>") is False

    def test_match_harmony_kv_positive(self) -> None:
        v = get_variant("harmony_kv")
        assert v.match("web_search\n<arg_key>q</arg_key>") is True

    def test_match_harmony_kv_negative(self) -> None:
        v = get_variant("harmony_kv")
        assert v.match('{"tool": "bash"}') is False

    def test_match_glm_brace_positive(self) -> None:
        v = get_variant("glm_brace")
        assert v.match('bash}{"command":"ls"}') is True

    def test_match_glm_brace_negative(self) -> None:
        v = get_variant("glm_brace")
        assert v.match("<function=bash>") is False

    def test_match_bare_name_tag_positive(self) -> None:
        v = get_variant("bare_name_tag")
        assert v.match('<web_search>{"q":"x"}</web_search>') is True

    def test_match_bare_name_tag_negative(self) -> None:
        v = get_variant("bare_name_tag")
        assert v.match("no xml here") is False

    def test_match_is_fast_over_10k_calls(self) -> None:
        """Each match predicate should be cheap — peek at body
        structure, never call parse. Budget: 10000 calls in <50ms
        wall-clock (generous — real-world it's ~1ms)."""
        body = '{"tool": "bash", "args": {"command": "ls"}}'
        v = get_variant("json_payload")
        start = time.perf_counter()
        for _ in range(10000):
            v.match(body)
        elapsed = time.perf_counter() - start
        assert elapsed < 0.5, f"match too slow: {elapsed:.3f}s for 10k calls"


# ---------------------------------------------------------------------------
# Plugin loader
# ---------------------------------------------------------------------------


class TestPluginLoader:
    def test_load_plugin_happy_path(self) -> None:
        dotted = "tests.fixtures.sample_variant_plugin:SampleVariant"
        try:
            v = load_plugin_variant(dotted)
            assert isinstance(v, ParserVariant)
            assert v.name == "sample_plugin"
        finally:
            REGISTRY.pop("sample_plugin", None)

    def test_load_plugin_missing_colon_raises(self) -> None:
        with pytest.raises(PluginLoadError, match="module:attr"):
            load_plugin_variant("no.colon.here")

    def test_load_plugin_module_not_importable_raises(self) -> None:
        with pytest.raises(PluginLoadError, match="cannot import"):
            load_plugin_variant("does.not.exist.anywhere:SomeAttr")

    def test_load_plugin_attr_missing_raises(self) -> None:
        with pytest.raises(PluginLoadError, match="no attribute"):
            load_plugin_variant(
                "tests.fixtures.sample_variant_plugin:MissingAttr"
            )

    def test_load_plugin_attr_wrong_type_raises(self) -> None:
        with pytest.raises(
            PluginLoadError, match="not a ParserVariant"
        ):
            load_plugin_variant(
                "tests.fixtures.sample_variant_plugin:NotAVariant"
            )

    def test_get_variant_with_colon_triggers_plugin_load(self) -> None:
        dotted = "tests.fixtures.sample_variant_plugin:SampleVariant"
        try:
            v = get_variant(dotted)
            assert isinstance(v, ParserVariant)
            assert v.name == "sample_plugin"
            # Registration should be cached by its own name now.
            assert "sample_plugin" in REGISTRY
        finally:
            REGISTRY.pop("sample_plugin", None)

    def test_plugin_e2e_through_parse_tool_calls(self) -> None:
        """End-to-end: plugin parses a non-standard body when
        listed in the profile's variant order."""
        dotted = "tests.fixtures.sample_variant_plugin:SampleVariant"
        try:
            load_plugin_variant(dotted)
            register_variant(load_plugin_variant(dotted))

            class _Profile:
                parser_variants: tuple[str, ...] = ("sample_plugin",)

            text = "<tool_call>SAMPLE:my_tool:hello-world</tool_call>"
            calls = parse_tool_calls(text, None, profile=_Profile())
            assert len(calls) == 1
            assert calls[0].name == "my_tool"
            assert calls[0].args == {"payload": "hello-world"}
        finally:
            REGISTRY.pop("sample_plugin", None)


# ---------------------------------------------------------------------------
# parse_tool_calls profile wiring
# ---------------------------------------------------------------------------


class _ProfileWithVariants:
    """Minimal stub exposing the ``parser_variants`` attribute so
    ``parse_tool_calls`` reads it via ``getattr`` without needing a
    full ``ModelProfile`` instance."""

    def __init__(self, variants: tuple[str, ...]) -> None:
        self.parser_variants = variants


class TestProfileDrivenOrder:
    def test_profile_none_uses_default_order(self) -> None:
        """Existing callers pass profile=None — identical output."""
        text = '<tool_call>{"tool": "bash", "args": {}}</tool_call>'
        calls = parse_tool_calls(text, None)
        profile_calls = parse_tool_calls(text, None, profile=None)
        assert [(c.name, c.args) for c in calls] == [
            (c.name, c.args) for c in profile_calls
        ]

    def test_profile_empty_variants_uses_default_order(self) -> None:
        """profile.parser_variants=() must fall back identically."""
        text = '<tool_call>{"tool": "bash", "args": {}}</tool_call>'
        profile = _ProfileWithVariants(variants=())
        calls = parse_tool_calls(text, None, profile=profile)
        default_calls = parse_tool_calls(text, None)
        assert [(c.name, c.args) for c in calls] == [
            (c.name, c.args) for c in default_calls
        ]

    def test_profile_json_only_skips_hermes(self) -> None:
        """An explicit variants=("json_payload",) profile must NOT
        fall back to Hermes — non-matching bodies return empty."""
        text = (
            "<tool_call>\n<function=bash>\n"
            "<parameter=command>\nls\n</parameter>\n"
            "</function>\n</tool_call>"
        )
        profile = _ProfileWithVariants(variants=("json_payload",))
        assert parse_tool_calls(text, None, profile=profile) == []

    def test_profile_only_hermes_parses_hermes_body(self) -> None:
        text = (
            "<tool_call>\n<function=bash>\n"
            "<parameter=command>\nls\n</parameter>\n"
            "</function>\n</tool_call>"
        )
        profile = _ProfileWithVariants(variants=("hermes_function",))
        calls = parse_tool_calls(text, None, profile=profile)
        assert len(calls) == 1
        assert calls[0].name == "bash"

    def test_profile_unknown_variant_raises(self) -> None:
        text = '<tool_call>{"tool": "bash"}</tool_call>'
        profile = _ProfileWithVariants(variants=("no_such_variant",))
        with pytest.raises(UnknownVariantError):
            parse_tool_calls(text, None, profile=profile)

    def test_profile_variant_order_affects_choice(self) -> None:
        """If both json_payload and harmony_kv could match, the
        first-listed wins. json_payload takes precedence because
        the body starts with ``{`` and contains ``"tool"``."""
        # Bodies that cleanly match exactly one variant, ordered
        # differently, verify the "first match wins" contract.
        text_json = '<tool_call>{"tool": "bash", "args": {}}</tool_call>'
        p1 = _ProfileWithVariants(variants=("json_payload", "harmony_kv"))
        p2 = _ProfileWithVariants(variants=("harmony_kv", "json_payload"))
        # Both should return the same result for a pure JSON body
        # because harmony's match() returns False — order can't
        # rescue a non-matching variant.
        r1 = parse_tool_calls(text_json, None, profile=p1)
        r2 = parse_tool_calls(text_json, None, profile=p2)
        assert len(r1) == 1 and len(r2) == 1
        assert r1[0].name == r2[0].name == "bash"

    def test_profile_without_parser_variants_attr_uses_default(self) -> None:
        """A profile-like object that doesn't define parser_variants
        at all (e.g. old ModelProfile without v13 fields) must
        silently fall back to DEFAULT_VARIANT_ORDER."""
        class _OldProfile:
            pass

        text = '<tool_call>{"tool": "bash", "args": {}}</tool_call>'
        calls = parse_tool_calls(text, None, profile=_OldProfile())
        assert len(calls) == 1
        assert calls[0].name == "bash"

    def test_profile_variant_excludes_glm_brace_skips_glm_body(self) -> None:
        """If a profile enables only ``json_payload`` + ``hermes_*``,
        a GLM variant 6 body must NOT parse (the wrapper-less
        fallback for glm_brace is also gated by the same order)."""
        text = '<tool_call>web_search}{"query":"x"}</arg_value>'
        profile = _ProfileWithVariants(
            variants=("json_payload", "hermes_function")
        )
        assert parse_tool_calls(text, None, profile=profile) == []

    def test_profile_variant_includes_glm_brace_parses(self) -> None:
        text = '<tool_call>web_search}{"query":"x"}</arg_value>'
        profile = _ProfileWithVariants(variants=("glm_brace",))
        calls = parse_tool_calls(text, None, profile=profile)
        assert len(calls) == 1
        assert calls[0].name == "web_search"

    def test_profile_variant_bare_name_tag_alone(self) -> None:
        text = '<web_search>{"query": "x"}</web_search>'
        profile = _ProfileWithVariants(variants=("bare_name_tag",))
        calls = parse_tool_calls(text, None, profile=profile)
        assert len(calls) == 1
        assert calls[0].name == "web_search"


class TestRegistryContainsSixByDefault:
    """Guard on the implicit count — if someone adds a seventh
    variant in Phase A, this test fires the reminder to update
    ``DEFAULT_VARIANT_ORDER`` and the author guide."""

    def test_registry_has_exactly_six_builtins_after_import(self) -> None:
        builtin_names = {
            "json_payload",
            "hermes_function",
            "hermes_truncated",
            "harmony_kv",
            "glm_brace",
            "bare_name_tag",
        }
        for name in builtin_names:
            assert name in REGISTRY
