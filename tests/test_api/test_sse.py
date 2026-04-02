"""Tests for llm_code.api.sse — TDD: written before implementation."""

from llm_code.api.sse import parse_sse_events


def collect(raw: str) -> list[dict]:
    return list(parse_sse_events(raw))


# ---------------------------------------------------------------------------
# Single event
# ---------------------------------------------------------------------------

class TestSingleEvent:
    def test_simple_data(self):
        raw = 'data: {"type": "ping"}\n\n'
        events = collect(raw)
        assert events == [{"type": "ping"}]

    def test_data_without_trailing_newline(self):
        raw = 'data: {"type": "ping"}'
        events = collect(raw)
        assert events == [{"type": "ping"}]


# ---------------------------------------------------------------------------
# Multiple events
# ---------------------------------------------------------------------------

class TestMultipleEvents:
    def test_two_events(self):
        raw = (
            'data: {"seq": 1}\n\n'
            'data: {"seq": 2}\n\n'
        )
        events = collect(raw)
        assert events == [{"seq": 1}, {"seq": 2}]

    def test_three_events(self):
        raw = (
            'data: {"a": 1}\n\n'
            'data: {"b": 2}\n\n'
            'data: {"c": 3}\n\n'
        )
        assert collect(raw) == [{"a": 1}, {"b": 2}, {"c": 3}]


# ---------------------------------------------------------------------------
# Comment lines
# ---------------------------------------------------------------------------

class TestCommentLines:
    def test_comment_only_block_skipped(self):
        raw = ': keep-alive\n\ndata: {"x": 1}\n\n'
        assert collect(raw) == [{"x": 1}]

    def test_comment_within_block_skipped(self):
        raw = ': comment\ndata: {"x": 2}\n\n'
        assert collect(raw) == [{"x": 2}]

    def test_multiple_comments(self):
        raw = ': ping\n\n: pong\n\ndata: {"y": 3}\n\n'
        assert collect(raw) == [{"y": 3}]


# ---------------------------------------------------------------------------
# [DONE] sentinel
# ---------------------------------------------------------------------------

class TestDoneSentinel:
    def test_done_stops_iteration(self):
        raw = (
            'data: {"seq": 1}\n\n'
            'data: [DONE]\n\n'
            'data: {"seq": 2}\n\n'
        )
        events = collect(raw)
        assert events == [{"seq": 1}]

    def test_done_only(self):
        raw = 'data: [DONE]\n\n'
        assert collect(raw) == []

    def test_done_no_events_before(self):
        raw = ': comment\n\ndata: [DONE]\n\n'
        assert collect(raw) == []


# ---------------------------------------------------------------------------
# Multi-line data
# ---------------------------------------------------------------------------

class TestMultilineData:
    def test_two_data_lines_joined(self):
        # SSE spec: multiple data lines are joined with \n
        raw = 'data: {"part1":\ndata: "hello"}\n\n'
        events = collect(raw)
        assert events == [{"part1": "hello"}]

    def test_multiline_valid_json(self):
        raw = 'data: {"a": 1,\ndata:  "b": 2}\n\n'
        # {"a": 1, "b": 2} — note the space after the colon is stripped per field
        events = collect(raw)
        assert events == [{"a": 1, "b": 2}]


# ---------------------------------------------------------------------------
# CRLF separators
# ---------------------------------------------------------------------------

class TestCRLF:
    def test_crlf_line_endings(self):
        raw = 'data: {"crlf": true}\r\n\r\n'
        assert collect(raw) == [{"crlf": True}]

    def test_crlf_multiple_events(self):
        raw = 'data: {"n": 1}\r\n\r\ndata: {"n": 2}\r\n\r\n'
        assert collect(raw) == [{"n": 1}, {"n": 2}]

    def test_mixed_crlf_and_lf(self):
        raw = 'data: {"a": 1}\r\n\r\ndata: {"b": 2}\n\n'
        assert collect(raw) == [{"a": 1}, {"b": 2}]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_string(self):
        assert collect("") == []

    def test_whitespace_only(self):
        assert collect("   \n\n   ") == []

    def test_non_data_field_ignored(self):
        # event: and id: fields are not data — just skip
        raw = 'event: message\nid: 42\ndata: {"ok": true}\n\n'
        assert collect(raw) == [{"ok": True}]
