"""StreamParser profile hints — v13 Phase A.

Exercises the new kwargs on ``StreamParser.__init__``:

- ``custom_close_tags`` — additional close tags tried when
  ``</tool_call>`` is not visible.
- ``call_separator_chars`` — chars stripped after a custom close
  tag before the next ``<tool_call>`` search.
- ``standard_close_required_on`` — substrings that force the parser
  to wait for ``</tool_call>`` and ignore custom close tags.

Default behaviour (no kwargs, all ``None``) is documented in the
class docstring: custom_close_tags defaults to ``("</arg_value>",)``,
call_separator_chars to ``"→ \\t\\r\\n"``, and
standard_close_required_on to ``("<arg_key>",)`` — i.e. v2.2.5 GLM
variant 6 + Harmony variant 7 support continues to work out of the
box. Those baseline tests live elsewhere; here we only cover the
new kwarg paths.
"""
from __future__ import annotations

from llm_code.view.stream_parser import StreamEventKind, StreamParser


def _fire(parser: StreamParser, *chunks: str, flush: bool = True) -> list:
    events: list = []
    for chunk in chunks:
        events.extend(parser.feed(chunk))
    if flush:
        events.extend(parser.flush())
    return events


# ---------------------------------------------------------------------------
# Defaults preserve v2.2.5 behaviour
# ---------------------------------------------------------------------------


class TestDefaultsMatchLegacyGlmSupport:
    """With no kwargs, the parser still supports GLM variant 6
    (``</arg_value>`` close + U+2192 separator). This is the
    "empty-hints equals default GLM behaviour" requirement."""

    def test_no_kwargs_parses_variant_6(self) -> None:
        p = StreamParser()
        events = _fire(
            p,
            '<tool_call>web_search}{"query":"a"}</arg_value>',
        )
        tc = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        assert len(tc) == 1
        assert tc[0].tool_call is not None
        assert tc[0].tool_call.args == {"query": "a"}

    def test_no_kwargs_arrow_separator_consumed(self) -> None:
        p = StreamParser()
        events = _fire(
            p,
            '<tool_call>web_search}{"query":"a"}</arg_value>'
            "\u2192"
            '<tool_call>web_search}{"query":"b"}</arg_value>',
        )
        tc = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        assert len(tc) == 2

    def test_no_kwargs_variant_7_waits_for_tool_call_close(self) -> None:
        """``<arg_key>`` in the buffer must prevent ``</arg_value>``
        from closing the block early — default behaviour."""
        p = StreamParser()
        events = _fire(
            p,
            "<tool_call>web_search\n"
            "<arg_key>query</arg_key><arg_value>news</arg_value>"
            "<arg_key>max_results</arg_key><arg_value>5</arg_value>"
            "</tool_call>",
        )
        tc = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        assert len(tc) == 1
        assert tc[0].tool_call.args == {"query": "news", "max_results": 5}


# ---------------------------------------------------------------------------
# Standard close wins
# ---------------------------------------------------------------------------


class TestStandardCloseWins:
    def test_standard_close_wins_when_both_present(self) -> None:
        """``</tool_call>`` beats ``</arg_value>`` when both appear in
        the buffer, regardless of order — variant 7 bodies contain
        ``</arg_value>`` tags legitimately and must not be truncated."""
        p = StreamParser(custom_close_tags=("</arg_value>",))
        events = _fire(
            p,
            "<tool_call>\n"
            "web_search\n"
            "<arg_key>query</arg_key>\n"
            "<arg_value>news</arg_value>\n"
            "</tool_call>",
        )
        tc = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        assert len(tc) == 1
        assert tc[0].tool_call is not None
        assert tc[0].tool_call.args == {"query": "news"}

    def test_standard_close_wins_with_explicit_empty_hints(self) -> None:
        """Explicit ``()`` disables custom close support entirely —
        only ``</tool_call>`` counts."""
        p = StreamParser(
            custom_close_tags=(),
            call_separator_chars="",
            standard_close_required_on=(),
        )
        events = _fire(
            p,
            '<tool_call>{"tool": "bash", "args": {"command": "ls"}}</tool_call>',
        )
        tc = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        assert len(tc) == 1
        assert tc[0].tool_call.name == "bash"


# ---------------------------------------------------------------------------
# required-on blocks early close
# ---------------------------------------------------------------------------


class TestStandardCloseRequiredOn:
    def test_required_on_prevents_custom_close_firing(self) -> None:
        """When a required-on substring is in the buffer, custom
        close tags are ignored — parser waits for ``</tool_call>``."""
        p = StreamParser(
            custom_close_tags=("</X>",),
            standard_close_required_on=("MARKER",),
        )
        # Feed only the partial buffer — has MARKER and </X> but no
        # </tool_call>. Must NOT emit a tool_call event yet.
        events = p.feed("<tool_call>NAME\nMARKER inside</X> more content")
        tc = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        assert tc == []
        # Close the block — now the parser should complete.
        events = p.feed("</tool_call>")
        tc = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        # Body doesn't parse cleanly — sentinel event emitted.
        assert len(tc) == 1

    def test_required_on_absent_allows_custom_close(self) -> None:
        """Without the required-on marker, custom close fires."""
        p = StreamParser(
            custom_close_tags=("</arg_value>",),
            standard_close_required_on=("<arg_key>",),
            call_separator_chars="",
        )
        events = _fire(
            p,
            '<tool_call>web_search}{"query":"x"}</arg_value>',
        )
        tc = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        assert len(tc) == 1

    def test_multiple_required_on_substrings_any_match_blocks(self) -> None:
        """The required-on list is ``any()``: any one substring is
        enough to force waiting."""
        p = StreamParser(
            custom_close_tags=("</CLOSE>",),
            standard_close_required_on=("ALPHA", "BETA"),
        )
        events = p.feed("<tool_call>body with BETA in it</CLOSE>")
        tc = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        assert tc == []


# ---------------------------------------------------------------------------
# Custom close with separator strip
# ---------------------------------------------------------------------------


class TestCustomCloseWithSeparator:
    def test_separator_stripped_after_custom_close(self) -> None:
        """Use the GLM variant 6 body shape (which actually parses
        when the custom close is ``</arg_value>``). The separator
        ``|`` is custom for this test — verify it gets consumed."""
        p = StreamParser(
            custom_close_tags=("</arg_value>",),
            call_separator_chars="|",
            standard_close_required_on=(),
        )
        events = _fire(
            p,
            '<tool_call>web_search}{"query":"a"}</arg_value>'
            "||||"
            '<tool_call>web_search}{"query":"b"}</arg_value>',
        )
        tc = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        assert len(tc) == 2
        calls = [e.tool_call for e in tc if e.tool_call is not None]
        assert [c.args["query"] for c in calls] == ["a", "b"]

    def test_separator_not_stripped_after_standard_close(self) -> None:
        """Only custom close tags trigger separator stripping. After
        the real ``</tool_call>`` any leading separator chars are
        preserved so they render as TEXT."""
        p = StreamParser(
            custom_close_tags=("</arg_value>",),
            call_separator_chars="|",
            standard_close_required_on=(),
        )
        events = _fire(
            p,
            '<tool_call>{"tool":"a","args":{}}</tool_call>|leftover',
        )
        text = "".join(e.text for e in events if e.kind == StreamEventKind.TEXT)
        # The "|" must remain as text (confirms no strip).
        assert "|leftover" in text

    def test_empty_separator_string_no_strip(self) -> None:
        p = StreamParser(
            custom_close_tags=("</arg_value>",),
            call_separator_chars="",
            standard_close_required_on=(),
        )
        events = _fire(
            p,
            '<tool_call>web_search}{"q":"x"}</arg_value>->trailing',
        )
        text = "".join(e.text for e in events if e.kind == StreamEventKind.TEXT)
        assert "->trailing" in text


# ---------------------------------------------------------------------------
# Custom close tag selection
# ---------------------------------------------------------------------------


class TestCustomCloseTagOrdering:
    def test_earliest_custom_close_wins(self) -> None:
        """When two custom close tags appear, the earliest position
        is used. We observe via the leftover text: content AFTER the
        early close stays visible as TEXT."""
        p = StreamParser(
            custom_close_tags=("</LATE>", "</EARLY>"),
            call_separator_chars="",
            standard_close_required_on=(),
        )
        events = _fire(
            p,
            '<tool_call>{"tool":"a","args":{}}</EARLY>MID</LATE>after',
        )
        text = "".join(e.text for e in events if e.kind == StreamEventKind.TEXT)
        # After </EARLY> close, MID</LATE>after is TEXT.
        assert "MID</LATE>after" in text

    def test_no_custom_close_waits_for_more_data(self) -> None:
        p = StreamParser(
            custom_close_tags=("</arg_value>",),
            standard_close_required_on=(),
        )
        # Open the block but never close — buffer stays.
        events = p.feed('<tool_call>web_search}{"q":"x"}')
        tc = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        assert tc == []
        # Delivering the close now completes it.
        events = p.feed("</arg_value>")
        tc = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        assert len(tc) == 1
        assert tc[0].tool_call is not None

    def test_custom_close_split_across_chunks(self) -> None:
        p = StreamParser(
            custom_close_tags=("</arg_value>",),
            call_separator_chars="",
            standard_close_required_on=(),
        )
        events: list = []
        for chunk in (
            '<tool_call>web_search}{"query":"a"',
            "}</arg_",
            "value>",
        ):
            events.extend(p.feed(chunk))
        events.extend(p.flush())
        tc = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        assert len(tc) == 1
        assert tc[0].tool_call is not None
        assert tc[0].tool_call.name == "web_search"


# ---------------------------------------------------------------------------
# No hints = pure default TEXT behaviour outside tool_call
# ---------------------------------------------------------------------------


class TestNonToolCallBehaviourUnaffected:
    def test_plain_text_still_passes(self) -> None:
        p = StreamParser(
            custom_close_tags=("</X>",),
            call_separator_chars="|",
            standard_close_required_on=("MARKER",),
        )
        events = p.feed("hello world")
        text = "".join(e.text for e in events if e.kind == StreamEventKind.TEXT)
        assert text == "hello world"

    def test_think_block_still_parses(self) -> None:
        p = StreamParser(
            custom_close_tags=("</X>",),
            standard_close_required_on=(),
        )
        events = p.feed("<think>reasoning</think>final")
        kinds = [e.kind for e in events]
        assert StreamEventKind.THINKING in kinds
        assert StreamEventKind.TEXT in kinds


# ---------------------------------------------------------------------------
# Empty hints == Claude-like "only </tool_call>" semantics
# ---------------------------------------------------------------------------


class TestClaudeLikeEmptyHints:
    """A profile for a simpler provider passes empty tuples/string
    and gets standard-only behaviour. No GLM support, no variant 7
    guard. The ``</arg_value>`` inside a variant 7 body would close
    a block early — but such profiles only run on models that never
    emit variant 7 anyway, so this is the correct trade-off."""

    def test_variant_6_body_ignored_without_custom_close(self) -> None:
        """Without ``</arg_value>`` in the custom close list, a
        variant 6 stream never completes — buffer holds it until
        flush salvages."""
        p = StreamParser(
            custom_close_tags=(),
            call_separator_chars="",
            standard_close_required_on=(),
        )
        events = p.feed('<tool_call>web_search}{"q":"x"}</arg_value>')
        tc_live = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        assert tc_live == []
        # flush() will either salvage to TEXT or recover via
        # parse_tool_calls (variant 6 scanner). What matters: not a
        # silent drop.
        flush_events = p.flush()
        assert len(flush_events) >= 1


class TestRequiredOnUnionBounded:
    """Phase A risk mitigation R3: the union of
    ``requires_standard_close_when`` across enabled variants should
    stay small. StreamParser doesn't enforce a cap, but the built-in
    set is ``("<arg_key>",)`` only — guard that."""

    def test_default_required_on_is_single_substring(self) -> None:
        p = StreamParser()
        assert p._standard_close_required_on == ("<arg_key>",)


class TestExplicitOverrideEmptyTuple:
    """Passing ``()`` explicitly (not None) opts out of the default
    hint entirely. This lets a Claude profile disable GLM support
    with zero behaviour overhead."""

    def test_explicit_empty_tuple_disables_custom_close(self) -> None:
        p = StreamParser(custom_close_tags=())
        # With no custom close, an </arg_value> cannot terminate a
        # block — parser waits forever.
        events = p.feed('<tool_call>web_search}{"q":"x"}</arg_value>')
        tc = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        assert tc == []

    def test_explicit_empty_required_on_disables_guard(self) -> None:
        """With ``standard_close_required_on=()`` there's no guard
        against variant 7 ``<arg_key>`` — ``</arg_value>`` may fire
        early. This is expected: the profile author must set the
        guard when they know their model emits variant 7."""
        p = StreamParser(
            custom_close_tags=("</arg_value>",),
            standard_close_required_on=(),
            call_separator_chars="",
        )
        # A variant 7 body — the first </arg_value> closes early.
        events = p.feed(
            "<tool_call>web_search\n"
            "<arg_key>query</arg_key>\n"
            "<arg_value>news</arg_value>"
        )
        # Parser closes the block at the first </arg_value>. Since
        # parse_tool_calls on that partial body likely fails, a
        # sentinel TOOL_CALL with tool_call=None is emitted.
        tc = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        assert len(tc) == 1  # single event (sentinel or parsed)


class TestConstructorAcceptsKeywordsOnly:
    """All three new kwargs are keyword-only by design."""

    def test_positional_custom_close_tags_rejected(self) -> None:
        import pytest
        with pytest.raises(TypeError):
            # Passing implicit_thinking positionally is already
            # blocked by the * in __init__; custom_close_tags too.
            StreamParser(False, None, ("</X>",))  # type: ignore[misc]
