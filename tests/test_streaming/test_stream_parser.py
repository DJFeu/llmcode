"""Canonical stream parser: state machine that both the TUI render
path and the runtime dispatch path consume."""
from __future__ import annotations

from llm_code.streaming.stream_parser import (
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
