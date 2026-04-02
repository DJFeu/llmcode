"""Tests for ContextCompressor (4-level progressive compression)."""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from llm_code.api.types import Message, TextBlock, ToolResultBlock, ToolUseBlock
from llm_code.runtime.session import Session
from llm_code.runtime.compressor import ContextCompressor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(messages: list[Message]) -> Session:
    s = Session.create(Path("/tmp/test"))
    return dataclasses.replace(s, messages=tuple(messages))


def _text_msg(role: str, text: str) -> Message:
    return Message(role=role, content=(TextBlock(text=text),))


def _tool_result_msg(tool_use_id: str, content: str) -> Message:
    return Message(role="user", content=(ToolResultBlock(tool_use_id=tool_use_id, content=content),))


def _tool_use_msg(tool_id: str, name: str, args: dict) -> Message:
    return Message(role="assistant", content=(ToolUseBlock(id=tool_id, name=name, input=args),))


def _read_file_pair(file_path: str, content: str, idx: int) -> tuple[Message, Message]:
    """Return (assistant tool_use, user tool_result) pair for a read_file call."""
    tid = f"tid-{idx}"
    use = _tool_use_msg(tid, "read_file", {"path": file_path})
    result = _tool_result_msg(tid, content)
    return use, result


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def compressor() -> ContextCompressor:
    return ContextCompressor(max_result_chars=2000)


# ---------------------------------------------------------------------------
# Task 4 Tests
# ---------------------------------------------------------------------------

class TestNoCompression:
    def test_no_compression_needed(self, compressor: ContextCompressor) -> None:
        """Session under threshold is returned unchanged (same object)."""
        msgs = [_text_msg("user", "hello"), _text_msg("assistant", "hi")]
        session = _make_session(msgs)
        threshold = session.estimated_tokens() + 1000
        result = compressor.compress(session, threshold)
        assert result is session


class TestSnipCompact:
    def test_snip_truncates_long_tool_results(self, compressor: ContextCompressor) -> None:
        """Level 1: tool result content > max_result_chars gets truncated."""
        long_content = "x" * 5000
        msgs = [_tool_result_msg("t1", long_content)]
        session = _make_session(msgs)

        # Threshold is very tight so we force at least snip level
        result = compressor._snip_compact(session)

        # Check that the tool result was truncated
        result_msg = result.messages[0]
        block = result_msg.content[0]
        assert isinstance(block, ToolResultBlock)
        assert len(block.content) <= 2000

    def test_snip_short_results_unchanged(self, compressor: ContextCompressor) -> None:
        """Level 1: short tool results are left alone."""
        short_content = "short result"
        msgs = [_tool_result_msg("t1", short_content)]
        session = _make_session(msgs)

        result = compressor._snip_compact(session)
        block = result.messages[0].content[0]
        assert isinstance(block, ToolResultBlock)
        assert block.content == short_content

    def test_snip_preserves_text_blocks(self, compressor: ContextCompressor) -> None:
        """Level 1: text blocks are not altered."""
        msgs = [_text_msg("user", "hello " * 1000)]
        session = _make_session(msgs)
        result = compressor._snip_compact(session)
        block = result.messages[0].content[0]
        assert isinstance(block, TextBlock)
        assert block.text == "hello " * 1000


class TestMicroCompact:
    def test_micro_removes_stale_reads(self, compressor: ContextCompressor) -> None:
        """Level 2: two read_file calls for same file → first result removed."""
        use1, result1 = _read_file_pair("/app/foo.py", "content v1", 1)
        use2, result2 = _read_file_pair("/app/foo.py", "content v2", 2)
        msgs = [use1, result1, use2, result2]
        session = _make_session(msgs)

        result = compressor._micro_compact(session)

        # The first tool result for /app/foo.py should be gone
        all_blocks = [b for m in result.messages for b in m.content]
        tool_results = [b for b in all_blocks if isinstance(b, ToolResultBlock)]
        assert len(tool_results) == 1
        assert tool_results[0].content == "content v2"

    def test_micro_different_paths_kept(self, compressor: ContextCompressor) -> None:
        """Level 2: reads for different files are all preserved."""
        use1, result1 = _read_file_pair("/app/foo.py", "foo content", 1)
        use2, result2 = _read_file_pair("/app/bar.py", "bar content", 2)
        msgs = [use1, result1, use2, result2]
        session = _make_session(msgs)

        result = compressor._micro_compact(session)
        all_blocks = [b for m in result.messages for b in m.content]
        tool_results = [b for b in all_blocks if isinstance(b, ToolResultBlock)]
        assert len(tool_results) == 2

    def test_micro_non_read_file_preserved(self, compressor: ContextCompressor) -> None:
        """Level 2: bash tool results are not removed."""
        tid = "bash-1"
        use = _tool_use_msg(tid, "bash", {"command": "ls"})
        result_block = _tool_result_msg(tid, "output")
        msgs = [use, result_block]
        session = _make_session(msgs)

        result = compressor._micro_compact(session)
        all_blocks = [b for m in result.messages for b in m.content]
        tool_results = [b for b in all_blocks if isinstance(b, ToolResultBlock)]
        assert len(tool_results) == 1


class TestContextCollapse:
    def test_collapse_keeps_recent(self, compressor: ContextCompressor) -> None:
        """Level 3: messages beyond keep_recent are collapsed to summary lines."""
        msgs = []
        for i in range(20):
            msgs.append(_text_msg("user" if i % 2 == 0 else "assistant", f"msg {i}"))
        session = _make_session(msgs)

        result = compressor._context_collapse(session, keep_recent=6)

        # Should have exactly 6 recent messages + at least 1 summary/collapsed message
        assert len(result.messages) <= 7  # at most 1 summary + 6 recent
        # Last 6 messages from original should be preserved
        for orig, collapsed in zip(msgs[-6:], result.messages[-6:]):
            assert orig == collapsed

    def test_collapse_small_session_unchanged(self, compressor: ContextCompressor) -> None:
        """Level 3: session with <= keep_recent messages returned unchanged."""
        msgs = [_text_msg("user", f"msg {i}") for i in range(4)]
        session = _make_session(msgs)

        result = compressor._context_collapse(session, keep_recent=6)
        assert result is session

    def test_collapse_tool_calls_become_summary(self, compressor: ContextCompressor) -> None:
        """Level 3: old tool_use blocks are replaced with summary text."""
        tid = "t1"
        old_use = _tool_use_msg(tid, "read_file", {"path": "/app/foo.py"})
        old_result = _tool_result_msg(tid, "content")
        recent = [_text_msg("user", f"recent {i}") for i in range(6)]
        msgs = [old_use, old_result] + recent
        session = _make_session(msgs)

        result = compressor._context_collapse(session, keep_recent=6)

        # The first message(s) should be summary text (not original tool_use)
        first_blocks = result.messages[0].content
        assert any(isinstance(b, TextBlock) for b in first_blocks)
        # tool_use blocks in the first (summary) section should be gone
        old_blocks = [b for m in result.messages[:-6] for b in m.content]
        assert not any(isinstance(b, ToolUseBlock) for b in old_blocks)


class TestAutoCompact:
    def test_auto_compact_summary_message(self, compressor: ContextCompressor) -> None:
        """Level 4: massive session → summary + keep_recent messages."""
        msgs = [_text_msg("user" if i % 2 == 0 else "assistant", f"msg {i}") for i in range(20)]
        session = _make_session(msgs)

        result = compressor._auto_compact(session, keep_recent=4)

        # Should be 1 summary + 4 recent
        assert len(result.messages) == 5
        # First message is summary
        first = result.messages[0]
        assert first.role == "user"
        text_blocks = [b for b in first.content if isinstance(b, TextBlock)]
        assert any("[Previous conversation summary]" in b.text for b in text_blocks)
        # Last 4 messages match original last 4
        for orig, kept in zip(msgs[-4:], result.messages[-4:]):
            assert orig == kept

    def test_auto_compact_small_session_unchanged(self, compressor: ContextCompressor) -> None:
        """Level 4: session with <= keep_recent messages returned unchanged."""
        msgs = [_text_msg("user", "hi"), _text_msg("assistant", "hello")]
        session = _make_session(msgs)

        result = compressor._auto_compact(session, keep_recent=4)
        assert result is session


class TestProgressiveStopping:
    def test_stops_early_when_snip_sufficient(self, compressor: ContextCompressor) -> None:
        """compress() stops at level 1 when snip reduces below threshold."""
        long_content = "x" * 8000  # ~2000 tokens when 4chars/token
        msgs = [_tool_result_msg("t1", long_content)]
        session = _make_session(msgs)

        # After snip, tokens will drop to ~500; set threshold at 1000 so snip is enough
        threshold = 1000
        result = compressor.compress(session, threshold)

        # Result should have truncated tool result (snip was applied)
        block = result.messages[0].content[0]
        assert isinstance(block, ToolResultBlock)
        assert len(block.content) <= 2000

    def test_under_threshold_returns_same(self, compressor: ContextCompressor) -> None:
        """compress() returns same session when already under threshold."""
        msgs = [_text_msg("user", "hi")]
        session = _make_session(msgs)
        result = compressor.compress(session, max_tokens=100_000)
        assert result is session


class TestAllLevelsChain:
    def test_all_levels_chain(self, compressor: ContextCompressor) -> None:
        """Very large session needs all 4 levels — verify final result is small enough."""
        # Build a session that requires all levels to compress enough
        # Lots of messages with large tool results and repeated reads
        msgs = []
        for i in range(50):
            tid = f"t{i}"
            use = _tool_use_msg(tid, "read_file", {"path": f"/app/file{i % 3}.py"})
            result = _tool_result_msg(tid, "y" * 4000)
            msgs.append(use)
            msgs.append(result)
        # Add some text messages to pad further
        for i in range(20):
            msgs.append(_text_msg("user" if i % 2 == 0 else "assistant", "z" * 500))

        session = _make_session(msgs)
        original_tokens = session.estimated_tokens()
        assert original_tokens > 5000, "Session must be large for this test"

        # Compress to a very tight budget — should not raise
        result = compressor.compress(session, max_tokens=200)

        # The result must exist and be a valid session
        assert isinstance(result, Session)
        # The result must have fewer tokens than original
        assert result.estimated_tokens() < original_tokens
