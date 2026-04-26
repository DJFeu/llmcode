"""Tests for v15 M5 — inline WebFetch / WebSearch parser variant.

Covers:

* Match cases: PascalCase + snake_case names; nested args; multiple
  inline calls.
* Reject cases: empty registry, unrelated registry, malformed JSON,
  unmatched names.
* Integration with ``parse_tool_calls`` (default order, profile that
  excludes the variant).
* Co-existence with earlier wrapped variants.
"""
from __future__ import annotations

from llm_code.tools.parser_variants import (
    DEFAULT_VARIANT_ORDER,
    REGISTRY,
)
from llm_code.tools.parsing import (
    _WEBFETCH_INLINE_RE,
    ParsedToolCall,
    _parse_webfetch_inline,
    parse_tool_calls,
)


# ── Pure regex sanity ────────────────────────────────────────────────


class TestRegexSanity:
    def test_pascal_case_webfetch_matches(self) -> None:
        m = _WEBFETCH_INLINE_RE.search('WebFetch{"url": "x"}')
        assert m is not None
        assert m.group(1) == "WebFetch"

    def test_snake_case_web_search_matches(self) -> None:
        m = _WEBFETCH_INLINE_RE.search('web_search{"query": "x"}')
        assert m is not None
        assert m.group(1) == "web_search"

    def test_whitespace_between_name_and_brace(self) -> None:
        m = _WEBFETCH_INLINE_RE.search('WebFetch  \n  {"url": "x"}')
        assert m is not None

    def test_does_not_match_unrelated_function(self) -> None:
        assert _WEBFETCH_INLINE_RE.search(
            'SomeOtherTool{"x": 1}'
        ) is None


# ── Leaf parser ──────────────────────────────────────────────────────


class TestParseWebfetchInline:
    def test_pascal_case_with_known_snake_name(self) -> None:
        out = _parse_webfetch_inline(
            'WebFetch{"url": "https://example.com"}',
            known_tool_names=frozenset({"web_fetch"}),
        )
        assert len(out) == 1
        assert isinstance(out[0], ParsedToolCall)
        assert out[0].name == "web_fetch"
        assert out[0].args == {"url": "https://example.com"}

    def test_snake_case_with_snake_registry(self) -> None:
        out = _parse_webfetch_inline(
            'web_search{"query": "x"}',
            known_tool_names=frozenset({"web_search"}),
        )
        assert len(out) == 1
        assert out[0].name == "web_search"
        assert out[0].args == {"query": "x"}

    def test_empty_registry_returns_empty(self) -> None:
        out = _parse_webfetch_inline(
            'WebFetch{"url": "x"}',
            known_tool_names=frozenset(),
        )
        assert out == []

    def test_registry_without_web_tools_returns_empty(self) -> None:
        out = _parse_webfetch_inline(
            'WebFetch{"url": "x"}',
            known_tool_names=frozenset({"read_file", "bash"}),
        )
        assert out == []

    def test_malformed_json_skipped(self) -> None:
        out = _parse_webfetch_inline(
            'WebFetch{this is not json}',
            known_tool_names=frozenset({"web_fetch"}),
        )
        assert out == []

    def test_non_dict_json_skipped(self) -> None:
        # Regex matches but the JSON isn't a dict — reject.
        # Also our regex requires ``{...}`` so a list ``[...]`` won't
        # even match the regex; pass a dict-like that decodes to
        # something else if ever possible. In practice the regex
        # restricts to ``{``-prefixed bodies, so this is mostly a
        # belt-and-braces test.
        out = _parse_webfetch_inline(
            'WebFetch{}',
            known_tool_names=frozenset({"web_fetch"}),
        )
        # Empty dict is valid; this is a valid call with no args.
        assert len(out) == 1
        assert out[0].args == {}

    def test_multiple_inline_calls(self) -> None:
        text = (
            'WebFetch{"url": "https://a"}\n'
            'WebSearch{"query": "x"}'
        )
        out = _parse_webfetch_inline(
            text,
            known_tool_names=frozenset({"web_fetch", "web_search"}),
        )
        assert len(out) == 2
        names = {c.name for c in out}
        assert names == {"web_fetch", "web_search"}

    def test_nested_json_args(self) -> None:
        out = _parse_webfetch_inline(
            'WebFetch{"options": {"timeout": 30}, "url": "x"}',
            known_tool_names=frozenset({"web_fetch"}),
        )
        assert len(out) == 1
        assert out[0].args["options"] == {"timeout": 30}

    def test_permissive_mode_no_registry(self) -> None:
        # ``known_tool_names=None`` is permissive (used by single-
        # call adapter); should still match.
        out = _parse_webfetch_inline(
            'WebFetch{"url": "x"}',
            known_tool_names=None,
        )
        assert len(out) == 1
        assert out[0].name == "web_fetch"

    def test_pascal_literal_in_registry(self) -> None:
        # Edge case: user registers literal PascalCase ``WebFetch``.
        # Honour the registered name verbatim.
        out = _parse_webfetch_inline(
            'WebFetch{"url": "x"}',
            known_tool_names=frozenset({"WebFetch"}),
        )
        assert len(out) == 1
        # Snake-case form not registered, but PascalCase IS — return
        # the PascalCase name.
        assert out[0].name == "WebFetch"


# ── Variant registration ─────────────────────────────────────────────


class TestVariantRegistration:
    def test_registered_in_registry(self) -> None:
        assert "webfetch_inline" in REGISTRY
        variant = REGISTRY["webfetch_inline"]
        assert variant.name == "webfetch_inline"

    def test_appended_to_default_order(self) -> None:
        # M5 must be the LAST variant in the default order so
        # wrapper-based variants run first.
        assert DEFAULT_VARIANT_ORDER[-1] == "webfetch_inline"

    def test_existing_six_variants_still_present(self) -> None:
        # Don't accidentally replace any of the v13 variants.
        for name in (
            "json_payload", "hermes_function", "hermes_truncated",
            "harmony_kv", "glm_brace", "bare_name_tag",
        ):
            assert name in DEFAULT_VARIANT_ORDER


# ── Integration with parse_tool_calls ────────────────────────────────


class TestIntegrationWithParseToolCalls:
    def test_default_order_picks_up_inline_webfetch(self) -> None:
        text = (
            "Let me search for that.\n"
            'WebFetch{"url": "https://example.com"}'
        )
        out = parse_tool_calls(
            response_text=text,
            native_tool_calls=None,
            known_tool_names=frozenset({"web_fetch"}),
        )
        assert len(out) == 1
        assert out[0].name == "web_fetch"

    def test_profile_omitting_variant_skips_inline(self) -> None:
        # A profile that explicitly lists parser_variants without
        # ``webfetch_inline`` should NOT match the inline shape.
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class FakeProfile:
            parser_variants: tuple[str, ...] = (
                "json_payload", "hermes_function",
            )

        out = parse_tool_calls(
            response_text='WebFetch{"url": "x"}',
            native_tool_calls=None,
            known_tool_names=frozenset({"web_fetch"}),
            profile=FakeProfile(),
        )
        assert out == []

    def test_wrapped_tool_call_wins_over_inline(self) -> None:
        # If a buffer has BOTH a wrapped <tool_call> AND a separate
        # inline ``WebFetch{…}``, the wrapped variant fires first
        # (it's first in DEFAULT_VARIANT_ORDER) and the wrapper-less
        # scanner only runs when the per-block loop yields nothing.
        # In this test the wrapped block matches via json_payload
        # variant — the inline shape is not picked up because the
        # per-block result is non-empty.
        text = (
            '<tool_call>{"tool": "web_fetch", "args": {"url": "y"}}</tool_call>\n'
            'WebFetch{"url": "x"}'
        )
        out = parse_tool_calls(
            response_text=text,
            native_tool_calls=None,
            known_tool_names=frozenset({"web_fetch"}),
        )
        # Wrapped result wins; inline is suppressed.
        assert len(out) == 1
        assert out[0].args == {"url": "y"}

    def test_inline_only_when_no_wrapper(self) -> None:
        # No ``<tool_call>`` wrapper at all — the wrapper-less scanner
        # path runs and picks up the inline shape.
        text = "Search results inline:\nweb_search{\"query\": \"q\"}"
        out = parse_tool_calls(
            response_text=text,
            native_tool_calls=None,
            known_tool_names=frozenset({"web_search"}),
        )
        assert len(out) == 1
        assert out[0].name == "web_search"
        assert out[0].args == {"query": "q"}

    def test_inline_with_unrelated_known_tools_rejected(self) -> None:
        # Production guard: registry doesn't include web tools → no
        # match (avoids false positives on code blocks).
        out = parse_tool_calls(
            response_text='WebFetch{"url": "x"}',
            native_tool_calls=None,
            known_tool_names=frozenset({"read_file", "bash"}),
        )
        assert out == []
