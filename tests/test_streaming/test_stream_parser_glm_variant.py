"""StreamParser handling of GLM-5.1 variant 6 tool calls (v2.2.4).

Adds:

1. ``</arg_value>`` close tag treated as a valid end for the
   ``_in_tool_call`` state (the standard parser waits for
   ``</tool_call>`` and hangs forever on glm-5.1 output).
2. U+2192 ``→`` separator consumed between chained GLM tool calls so
   the next ``<tool_call>`` is seen cleanly.
3. ``flush()`` recovery — if the stream ends while still inside a
   ``<tool_call>`` block but the body parses as variant 6, emit
   those tool calls instead of downgrading to TEXT salvage.

Reproduces the exact screenshot from the bug report:

    <tool_call>web_search}{"query":"今日熱門新聞 2026年4月","max_results":5}</arg_value>
    →<tool_call>web_search}{"query":"top news today April 2026","max_results":5}</arg_value>

v13 Phase C note: the GLM-specific hints no longer live in
``StreamParser`` class defaults. Each test constructs the parser via
``_glm_parser()`` below which injects the hints the GLM profile
(``examples/model_profiles/65-glm-5.1.toml``) declares in its
``[parser_hints]`` section.
"""
from __future__ import annotations

from llm_code.view.stream_parser import StreamEventKind, StreamParser

_GLM_CUSTOM_CLOSE = ("</arg_value>",)
_GLM_CALL_SEPARATOR = "\u2192 \t\r\n"
_GLM_STANDARD_CLOSE_REQUIRED_ON = ("<arg_key>",)


def _glm_parser() -> StreamParser:
    """Build a ``StreamParser`` with the GLM-5.1 parser hints."""
    return StreamParser(
        custom_close_tags=_GLM_CUSTOM_CLOSE,
        call_separator_chars=_GLM_CALL_SEPARATOR,
        standard_close_required_on=_GLM_STANDARD_CLOSE_REQUIRED_ON,
    )


def _fire(parser: StreamParser, *chunks: str, flush: bool = True) -> list:
    events: list = []
    for chunk in chunks:
        events.extend(parser.feed(chunk))
    if flush:
        events.extend(parser.flush())
    return events


class TestArgValueAsClose:
    def test_single_call_closes_on_arg_value(self) -> None:
        """``</arg_value>`` must terminate the tool_call state mid-
        stream — the rest of the buffer returns to normal TEXT
        handling immediately."""
        p = _glm_parser()
        events = _fire(
            p,
            '<tool_call>web_search}{"query":"news","max_results":3}</arg_value>',
            "trailing prose",
        )
        tool_events = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        text_events = [e for e in events if e.kind == StreamEventKind.TEXT]
        assert len(tool_events) == 1
        assert tool_events[0].tool_call is not None
        assert tool_events[0].tool_call.name == "web_search"
        assert tool_events[0].tool_call.args == {"query": "news", "max_results": 3}
        # Trailing text after the close must reach the user.
        trailing = "".join(e.text for e in text_events)
        assert "trailing prose" in trailing

    def test_arrow_separator_consumed(self) -> None:
        """Chained GLM calls separated by U+2192 — the arrow and any
        whitespace around it must be swallowed so the second
        ``<tool_call>`` is recognised."""
        p = _glm_parser()
        events = _fire(
            p,
            '<tool_call>web_search}{"query":"a"}</arg_value>',
            "→",
            '<tool_call>web_search}{"query":"b"}</arg_value>',
        )
        tool_events = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        assert len(tool_events) == 2
        queries = [e.tool_call.args["query"] for e in tool_events]
        assert queries == ["a", "b"]

    def test_standard_tool_call_close_still_wins(self) -> None:
        """If the standard ``</tool_call>`` close arrives before an
        ``</arg_value>``, use it (don't regress well-formed
        emissions)."""
        p = _glm_parser()
        events = _fire(
            p,
            '<tool_call>\n{"tool":"read_file","args":{"path":"/x"}}\n</tool_call>',
        )
        tool_events = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        assert len(tool_events) == 1
        assert tool_events[0].tool_call.name == "read_file"


class TestChunkedStreaming:
    def test_close_tag_split_across_chunks(self) -> None:
        """``</arg_value>`` straddling a chunk boundary must still
        trigger the close once both halves are fed."""
        p = _glm_parser()
        events: list = []
        for chunk in (
            '<tool_call>web_search}{"query":"news",',
            '"max_results":5}</arg_',
            'value>',
        ):
            events.extend(p.feed(chunk))
        events.extend(p.flush())
        tool_events = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        assert len(tool_events) == 1
        assert tool_events[0].tool_call.args["max_results"] == 5

    def test_arrow_split_from_next_tool_call(self) -> None:
        """The separator and the next ``<tool_call>`` arrive in
        different chunks — the arrow must not block the next
        recognition."""
        p = _glm_parser()
        events: list = []
        for chunk in (
            '<tool_call>web_search}{"query":"a"}</arg_value>',
            "→",
            '<tool_call>web_search}{"query":"b"}</arg_value>',
        ):
            events.extend(p.feed(chunk))
        events.extend(p.flush())
        calls = [e.tool_call for e in events if e.kind == StreamEventKind.TOOL_CALL]
        assert [c.args["query"] for c in calls] == ["a", "b"]


class TestFlushRecovery:
    def test_end_to_end_glm_variant_without_close_tag(self) -> None:
        """Exact screenshot scenario: each ``<tool_call>`` block is
        closed with ``</arg_value>`` (not ``</tool_call>``), sibling
        calls separated by U+2192. All three tool calls must reach
        the caller — via feed()'s streaming emits if the step parser
        can close them on the fly, or via flush() recovery if the
        stream terminated mid-block."""
        p = _glm_parser()
        events = _fire(
            p,
            '<tool_call>web_search}{"query":"今日熱門新聞 2026年4月","max_results":5}</arg_value>'
            "→"
            '<tool_call>web_search}{"query":"top news today April 2026","max_results":5}</arg_value>'
            "→"
            '<tool_call>web_search}{"query":"breaking news today","max_results":5}</arg_value>'
            "→",
        )
        tool_events = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        text_events = [e for e in events if e.kind == StreamEventKind.TEXT]
        assert len(tool_events) == 3
        assert [e.tool_call.name for e in tool_events] == ["web_search"] * 3
        # Critically: no TEXT salvage — the user must see tool results,
        # not the raw XML as chat output.
        assert text_events == []

    def test_flush_recovers_truncated_final_glm_call(self) -> None:
        """Stream ends AFTER an opening ``<tool_call>`` but BEFORE
        the ``</arg_value>`` close arrives. flush() should parse
        what it can (none in this case — the body is incomplete),
        or fall back to TEXT salvage. Guard against the old bug
        where everything was silently dropped."""
        p = _glm_parser()
        events: list = []
        events.extend(p.feed(
            '<tool_call>web_search}{"query":"unterminated'
        ))
        events.extend(p.flush())
        # Either the body parsed (variant 6 recovery) or the text
        # was salvaged. What must NOT happen: silent drop.
        tool_or_text = [
            e for e in events
            if e.kind in (StreamEventKind.TOOL_CALL, StreamEventKind.TEXT)
        ]
        assert len(tool_or_text) >= 1

    def test_flush_still_salvages_text_when_parse_fails(self) -> None:
        """Regression guard — an unterminated ``<tool_call>`` whose
        body is NOT variant 6 still falls through to the legacy
        TEXT salvage so the original bug fix
        (unterminated-tool-call swallowing news items) keeps
        working."""
        p = _glm_parser()
        p.feed("<tool_call>1. **News item one**\n2. **News item two**\n")
        events = p.flush()
        tool_events = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        text_events = [e for e in events if e.kind == StreamEventKind.TEXT]
        assert tool_events == []
        assert len(text_events) == 1
        assert "News item one" in text_events[0].text
        assert "News item two" in text_events[0].text
        # ``<tool_call>`` prefix stripped
        assert "<tool_call>" not in text_events[0].text
