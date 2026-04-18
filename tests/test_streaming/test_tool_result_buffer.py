"""M1: ToolResultBuffer with backpressure + truncation."""
from __future__ import annotations

from llm_code.streaming.tool_result_buffer import ToolResultBuffer


class TestSmallBuffer:
    def test_append_under_cap(self) -> None:
        buf = ToolResultBuffer(max_chars=100, max_lines=10)
        buf.append("hello\n")
        buf.append("world\n")
        assert buf.text() == "hello\nworld\n"
        assert buf.truncated is False

    def test_char_cap_truncates(self) -> None:
        buf = ToolResultBuffer(max_chars=10)
        buf.append("abcdefghij")
        buf.append("KLMNOP")  # overflows
        out = buf.text()
        assert len(out) <= 10 + len(buf.TRUNCATION_MARKER)
        assert buf.truncated is True

    def test_line_cap_truncates(self) -> None:
        buf = ToolResultBuffer(max_chars=10_000, max_lines=3)
        for i in range(5):
            buf.append(f"line{i}\n")
        lines = buf.text().split("\n")
        # Real data lines — drop trailing empties from split + the marker.
        data_lines = [
            ln for ln in lines if ln and not ln.startswith("...")
        ]
        assert len(data_lines) <= 3
        assert buf.truncated is True


class TestFlushAndClear:
    def test_flush_returns_buffer_and_clears(self) -> None:
        buf = ToolResultBuffer(max_chars=100)
        buf.append("x\n")
        buf.append("y\n")
        out = buf.flush()
        assert out == "x\ny\n"
        assert buf.text() == ""
        assert buf.truncated is False

    def test_size_and_line_count(self) -> None:
        buf = ToolResultBuffer(max_chars=100)
        buf.append("a\nb\nc\n")
        assert buf.size == 6
        assert buf.line_count == 3


class TestTruncationMarker:
    def test_marker_appended_on_cap_hit(self) -> None:
        buf = ToolResultBuffer(max_chars=5)
        buf.append("abcdefgh")
        assert buf.TRUNCATION_MARKER in buf.text()

    def test_marker_not_present_when_under_cap(self) -> None:
        buf = ToolResultBuffer(max_chars=100)
        buf.append("small")
        assert buf.TRUNCATION_MARKER not in buf.text()
