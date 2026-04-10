"""Canonical stream parser: state machine that both the TUI render
path and the runtime dispatch path consume."""
from __future__ import annotations

from llm_code.tui.stream_parser import (
    StreamEventKind,
    StreamParser,
)


class TestStreamParserTextOnly:
    def test_plain_text_passes_through(self) -> None:
        p = StreamParser()
        events = p.feed("hello world")
        assert len(events) == 1
        assert events[0].kind == StreamEventKind.TEXT
        assert events[0].text == "hello world"

    def test_empty_feed_emits_nothing(self) -> None:
        p = StreamParser()
        assert p.feed("") == []

    def test_plain_text_split_across_chunks(self) -> None:
        p = StreamParser()
        events1 = p.feed("hello ")
        events2 = p.feed("world")
        text = "".join(e.text for e in events1 + events2 if e.kind == StreamEventKind.TEXT)
        assert text == "hello world"


class TestStreamParserThinkBlock:
    def test_think_block_emits_thinking_event(self) -> None:
        p = StreamParser()
        events = p.feed("<think>reasoning</think>final")
        kinds = [e.kind for e in events]
        assert StreamEventKind.THINKING in kinds
        assert StreamEventKind.TEXT in kinds
        thinking = next(e for e in events if e.kind == StreamEventKind.THINKING)
        text = next(e for e in events if e.kind == StreamEventKind.TEXT)
        assert thinking.text == "reasoning"
        assert text.text == "final"

    def test_think_block_split_across_chunks(self) -> None:
        p = StreamParser()
        events1 = p.feed("<thi")
        events2 = p.feed("nk>reasoning</think>ok")
        all_events = events1 + events2
        assert any(
            e.kind == StreamEventKind.THINKING and e.text == "reasoning"
            for e in all_events
        )
        assert any(
            e.kind == StreamEventKind.TEXT and e.text == "ok"
            for e in all_events
        )

    def test_closing_tag_only_is_treated_as_implicit_think_end(self) -> None:
        """vLLM-served Qwen3 injects '<think>\\n' as the assistant prompt
        prefix, so the stream starts with thinking content and the first
        tag seen is </think>. StreamParser must treat everything before
        that first </think> as THINKING."""
        p = StreamParser()
        events = p.feed("implicit thinking</think>visible answer")
        kinds = [e.kind for e in events]
        assert StreamEventKind.THINKING in kinds
        assert StreamEventKind.TEXT in kinds
        thinking = next(e for e in events if e.kind == StreamEventKind.THINKING)
        text = next(e for e in events if e.kind == StreamEventKind.TEXT)
        assert thinking.text == "implicit thinking"
        assert text.text == "visible answer"


class TestStreamParserToolCalls:
    def test_full_hermes_form_emits_tool_call_event(self) -> None:
        p = StreamParser()
        text = (
            "<tool_call>\n<function=bash>\n"
            "<parameter=command>\nls\n</parameter>\n"
            "</function>\n</tool_call>"
        )
        events = p.feed(text)
        tc = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        assert len(tc) == 1
        assert tc[0].tool_call is not None
        assert tc[0].tool_call.name == "bash"
        assert tc[0].tool_call.args == {"command": "ls"}

    def test_truncated_form_with_parameters(self) -> None:
        p = StreamParser()
        text = (
            "<tool_call>bash>\n"
            "<parameter=command>\nls\n</parameter>\n"
            "</function></tool_call>"
        )
        events = p.feed(text)
        tc = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        assert len(tc) == 1
        assert tc[0].tool_call.name == "bash"
        assert tc[0].tool_call.args == {"command": "ls"}

    def test_truncated_form_with_json_args(self) -> None:
        p = StreamParser()
        text = (
            '<tool_call>web_search>'
            '{"args": {"query": "x"}}'
            '</tool_call>'
        )
        events = p.feed(text)
        tc = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        assert len(tc) == 1
        assert tc[0].tool_call.name == "web_search"
        assert tc[0].tool_call.args == {"query": "x"}

    def test_tool_call_split_across_chunks(self) -> None:
        p = StreamParser()
        events1 = p.feed("<tool_call>bash>{\"args\":")
        events2 = p.feed(' {"command": "ls"}}</tool_call>')
        all_events = events1 + events2
        tc = [e for e in all_events if e.kind == StreamEventKind.TOOL_CALL]
        assert len(tc) == 1
        assert tc[0].tool_call.name == "bash"
        assert tc[0].tool_call.args == {"command": "ls"}

    def test_tool_call_tag_split_across_chunks(self) -> None:
        """Even the opening <tool_call> tag can straddle a chunk boundary."""
        p = StreamParser()
        events1 = p.feed("<tool_c")
        events2 = p.feed('all>bash>{"args": {"command": "ls"}}</tool_call>')
        all_events = events1 + events2
        tc = [e for e in all_events if e.kind == StreamEventKind.TOOL_CALL]
        assert len(tc) == 1
        assert tc[0].tool_call.name == "bash"


class TestStreamParserInterleaving:
    def test_text_then_think_then_tool_call(self) -> None:
        p = StreamParser()
        text = (
            "Let me think. "
            "<think>need to list files</think>"
            '<tool_call>bash>{"args": {"command": "ls"}}</tool_call>'
            "Done."
        )
        events = p.feed(text)
        kinds = [e.kind for e in events]
        assert StreamEventKind.TEXT in kinds
        assert StreamEventKind.THINKING in kinds
        assert StreamEventKind.TOOL_CALL in kinds
        thinking_events = [e for e in events if e.kind == StreamEventKind.THINKING]
        assert any("need to list files" in e.text for e in thinking_events)
        tc_events = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        assert len(tc_events) == 1
        assert tc_events[0].tool_call.name == "bash"

    def test_flush_emits_trailing_plain_text(self) -> None:
        # The reserve window may hold back "partial" during feed —
        # flush must release it so callers see the full stream.
        p = StreamParser()
        feed_events = p.feed("<think>done</think>partial")
        flush_events = p.flush()
        all_text = "".join(
            e.text for e in (feed_events + flush_events) if e.kind == StreamEventKind.TEXT
        )
        assert all_text == "partial"

    def test_two_sequential_tool_calls(self) -> None:
        p = StreamParser()
        text = (
            '<tool_call>bash>{"args": {"command": "ls"}}</tool_call>'
            '<tool_call>read_file>{"args": {"file_path": "/tmp/x"}}</tool_call>'
        )
        events = p.feed(text)
        tc = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        assert len(tc) == 2
        assert tc[0].tool_call.name == "bash"
        assert tc[1].tool_call.name == "read_file"

    def test_unparseable_tool_call_block_still_emits_sentinel_event(self) -> None:
        """If the parser sees <tool_call>...</tool_call> but cannot parse
        the body (unknown format variant), it must still emit a TOOL_CALL
        event with ``tool_call=None``. Otherwise the TUI falls back to
        the 'thinking ate output' diagnostic, misleading users who are
        actually hitting a parser gap."""
        p = StreamParser()
        events = p.feed("<tool_call>garbage nonsense with no structure</tool_call>")
        tool_call_events = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        assert len(tool_call_events) == 1
        assert tool_call_events[0].tool_call is None

    def test_variant4_no_separator_emits_real_tool_call_event(self) -> None:
        """The captured 2026-04-08 Variant 4 must produce a real parsed
        TOOL_CALL event (not a sentinel), once the parser handles it."""
        p = StreamParser()
        events = p.feed(
            '<tool_call>web_search{"args": {"max_results": 5, "query": "x"}}</tool_call>'
        )
        tc = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        assert len(tc) == 1
        assert tc[0].tool_call is not None
        assert tc[0].tool_call.name == "web_search"
        assert tc[0].tool_call.args == {"max_results": 5, "query": "x"}


# ----- Unterminated tag salvage (flush behavior fix) -----

def test_flush_salvages_unterminated_tool_call_as_text() -> None:
    """Critical bug fix: an unterminated ``<tool_call>`` block at
    end of stream used to be silently dropped by flush(), which
    caused the TUI to lose any content the model had generated
    inside that tag. Reproduces the field report where the user
    asked for news and saw only the intro line — the items were
    inside a never-closed <tool_call> tag and vanished.
    """
    p = StreamParser()
    p.feed("<tool_call>")
    p.feed("1. **Item one** details\n")
    p.feed("2. **Item two**\n")
    # Stream ends without </tool_call>
    events = p.flush()
    text_events = [e for e in events if e.kind == StreamEventKind.TEXT]
    assert len(text_events) == 1
    assert "Item one" in text_events[0].text
    assert "Item two" in text_events[0].text
    # The opening <tool_call> marker must be stripped so the text
    # reads naturally in the chat widget
    assert "<tool_call>" not in text_events[0].text


def test_flush_salvage_preserves_intro_before_unclosed_tool_call() -> None:
    """The user scenario exactly: visible intro rendered cleanly,
    then an unclosed <tool_call> swallows the real answer content.
    Both the intro AND the salvaged body must reach the user."""
    p = StreamParser()
    events = []
    for chunk in (
        "根據搜尋結果,以下是今日三則熱門新聞:\n\n",
        "<tool_call>",
        "1. **美伊達成停火協議** — 詳細內容\n",
        "2. **鄭麗文訪中**\n",
        "3. **79 歲阿公擁資產堅決不給兒孫**\n",
    ):
        events.extend(p.feed(chunk))
    events.extend(p.flush())

    visible_text = "".join(e.text for e in events if e.kind == StreamEventKind.TEXT)
    # Intro MUST be present (rendered during streaming)
    assert "根據搜尋結果" in visible_text
    # All three news items MUST be recoverable (via flush salvage)
    assert "美伊達成停火協議" in visible_text
    assert "鄭麗文訪中" in visible_text
    assert "79 歲阿公" in visible_text


def test_flush_unterminated_think_preserved_as_thinking() -> None:
    """Complementary: unterminated <think> already preserved content
    before the fix, but only as THINKING events. Pin that behavior
    so a future refactor doesn't accidentally break it."""
    p = StreamParser()
    p.feed("<think>reasoning in progress")
    events = p.flush()
    thinking_events = [e for e in events if e.kind == StreamEventKind.THINKING]
    assert len(thinking_events) == 1
    assert "reasoning in progress" in thinking_events[0].text


def test_flush_empty_unterminated_tool_call_does_not_emit_empty_text() -> None:
    """Edge case: the parser entered tool_call state but the buffer
    contains only the opening marker (no body). Salvage should not
    emit an empty TEXT event."""
    p = StreamParser()
    p.feed("<tool_call>")
    events = p.flush()
    text_events = [e for e in events if e.kind == StreamEventKind.TEXT]
    assert text_events == []


def test_flush_state_cleared_after_salvage() -> None:
    """After flush, in_tool_call must be False and buffer empty
    so the same parser instance can be reused for another stream."""
    p = StreamParser()
    p.feed("<tool_call>leaked content")
    p.flush()
    assert p._in_tool_call is False
    assert p._buffer == ""
    # Reusable: next feed starts clean
    _ = p.feed("fresh text")
    p.flush()
    # The 'fresh text' eventually emerges as TEXT (either from feed
    # or the trailing flush — both are acceptable).


def test_flush_salvage_emits_warning_log(caplog) -> None:
    """The salvage fires a warning log so ``-v`` runs capture the
    event. Silent data loss is worse than loud data loss."""
    import logging
    p = StreamParser()
    p.feed("<tool_call>data that would have been lost")
    with caplog.at_level(logging.WARNING, logger="llm_code.tui.stream_parser"):
        p.flush()
    assert any(
        "unterminated <tool_call>" in r.message for r in caplog.records
    )


def test_flush_unterminated_think_also_emits_warning(caplog) -> None:
    import logging
    p = StreamParser()
    p.feed("<think>never closed")
    with caplog.at_level(logging.WARNING, logger="llm_code.tui.stream_parser"):
        p.flush()
    assert any(
        "unterminated <think>" in r.message for r in caplog.records
    )
