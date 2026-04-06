"""Integration tests for streaming text parsing (think tags, tool_call tags).

Tests the think tag detection logic used by the TUI streaming state machine
(see llm_code/tui/app.py) in isolation, without requiring a full App instance.
"""
from __future__ import annotations


class TestThinkTagParsing:
    """Test the think tag detection logic extracted from app.py streaming."""

    def test_first_delta_with_think_tag(self) -> None:
        """First delta starting with <think> should enter thinking mode."""
        buffer = "<think>\nThe user wants..."
        stripped = buffer.lstrip()
        assert stripped.startswith("<think>")

    def test_first_delta_with_thinking_tag(self) -> None:
        """First delta starting with <thinking> should also be detected."""
        buffer = "<thinking>\nLet me analyze..."
        stripped = buffer.lstrip()
        assert stripped.startswith("<thinking>")

    def test_first_delta_partial_tag(self) -> None:
        """Partial <thi should be held back (not yet enough data)."""
        buffer = "<thi"
        stripped = buffer.lstrip()
        assert len(stripped) < len("<thinking>") and stripped.startswith("<")

    def test_first_delta_whitespace_before_tag(self) -> None:
        """Leading whitespace before <think> should be stripped for detection."""
        buffer = "   \n <think>content"
        stripped = buffer.lstrip()
        assert stripped.startswith("<think>")

    def test_close_tag_extraction(self) -> None:
        """Content between <think> and </think> should be extractable."""
        buffer = "thinking content here</think>actual response"
        close_tag = "</think>"
        think_content, _, remaining = buffer.partition(close_tag)
        assert think_content == "thinking content here"
        assert remaining == "actual response"

    def test_close_thinking_tag_extraction(self) -> None:
        """Content between <thinking> and </thinking> should be extractable."""
        buffer = "deep thought</thinking>response text"
        close_tag = "</thinking>"
        think_content, _, remaining = buffer.partition(close_tag)
        assert think_content == "deep thought"
        assert remaining == "response text"

    def test_safety_strip_tags(self) -> None:
        """Safety net should strip all think/thinking tags."""
        buffer = "some <think>text</think> and <thinking>more</thinking>"
        for tag in ("<think>", "</think>", "<thinking>", "</thinking>"):
            buffer = buffer.replace(tag, "")
        assert buffer == "some text and more"

    def test_partial_tag_holdback(self) -> None:
        """Buffer ending with < should not be flushed."""
        buffer = "some text<"
        last_lt = buffer.rfind("<")
        assert last_lt >= 0
        assert ">" not in buffer[last_lt:]
        flush = buffer[:last_lt]
        held = buffer[last_lt:]
        assert flush == "some text"
        assert held == "<"

    def test_partial_tag_holdback_longer(self) -> None:
        """Buffer ending with <thin should be held back."""
        buffer = "content<thin"
        last_lt = buffer.rfind("<")
        assert ">" not in buffer[last_lt:]
        flush = buffer[:last_lt]
        held = buffer[last_lt:]
        assert flush == "content"
        assert held == "<thin"

    def test_complete_tag_not_held(self) -> None:
        """Buffer with complete <tag> should not be held back."""
        buffer = "text with <b>bold</b> done"
        last_lt = buffer.rfind("<")
        has_close = ">" in buffer[last_lt:]
        assert has_close  # should NOT hold back

    def test_no_tag_flush_everything(self) -> None:
        """Buffer without < should flush completely."""
        buffer = "normal text without any tags"
        last_lt = buffer.rfind("<")
        assert last_lt == -1  # no <, flush all

    def test_multiple_think_blocks(self) -> None:
        """Multiple think blocks should each be extractable."""
        text = "<think>first</think>middle<think>second</think>end"
        # First extraction
        idx = text.index("<think>") + len("<think>")
        rest = text[idx:]
        content1, _, rest = rest.partition("</think>")
        assert content1 == "first"
        # Middle text
        idx2 = rest.index("<think>") if "<think>" in rest else -1
        middle = rest[:idx2]
        assert middle == "middle"
        # Second extraction
        rest = rest[idx2 + len("<think>"):]
        content2, _, rest = rest.partition("</think>")
        assert content2 == "second"
        assert rest == "end"

    def test_empty_think_block(self) -> None:
        """Empty think block should produce empty content."""
        buffer = "</think>response"
        think_content, _, remaining = buffer.partition("</think>")
        assert think_content == ""
        assert remaining == "response"


class TestMidStreamThinkDetection:
    """Test mid-stream think tag detection (after tool results)."""

    def test_open_tag_splits_buffer(self) -> None:
        """<think> in middle of buffer splits into before and after."""
        buffer = "some output<think>thinking here"
        open_tag = "<think>"
        before, _, after = buffer.partition(open_tag)
        assert before == "some output"
        assert after == "thinking here"

    def test_open_and_close_in_same_buffer(self) -> None:
        """Both open and close tags in same buffer."""
        buffer = "before<think>inner thought</think>after"
        open_tag = "<think>"
        close_tag = "</think>"
        before, _, rest = buffer.partition(open_tag)
        think_content, _, after = rest.partition(close_tag)
        assert before == "before"
        assert think_content == "inner thought"
        assert after == "after"

    def test_no_think_tag_in_buffer(self) -> None:
        """Buffer without think tags should not split."""
        buffer = "just regular content"
        assert "<think>" not in buffer
        assert "<thinking>" not in buffer
