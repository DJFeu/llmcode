"""Tests for cache-aware compression in ContextCompressor."""
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
    tid = f"tid-{idx}"
    use = _tool_use_msg(tid, "read_file", {"path": file_path})
    result = _tool_result_msg(tid, content)
    return use, result


@pytest.fixture()
def compressor() -> ContextCompressor:
    return ContextCompressor(max_result_chars=2000)


# ---------------------------------------------------------------------------
# Test mark_as_cached
# ---------------------------------------------------------------------------

class TestMarkAsCached:
    def test_mark_as_cached_stores_indices(self, compressor: ContextCompressor) -> None:
        """mark_as_cached stores the given indices."""
        compressor.mark_as_cached({0, 1, 2})
        assert compressor._cached_indices == {0, 1, 2}

    def test_mark_as_cached_accumulates(self, compressor: ContextCompressor) -> None:
        """mark_as_cached accumulates across multiple calls."""
        compressor.mark_as_cached({0, 1})
        compressor.mark_as_cached({2, 3})
        assert compressor._cached_indices == {0, 1, 2, 3}

    def test_is_cached_false_by_default(self, compressor: ContextCompressor) -> None:
        """No messages are cached by default."""
        assert not compressor._is_cached(0)
        assert not compressor._is_cached(5)

    def test_is_cached_true_after_marking(self, compressor: ContextCompressor) -> None:
        """is_cached returns True after marking."""
        compressor.mark_as_cached({3})
        assert compressor._is_cached(3)
        assert not compressor._is_cached(4)


# ---------------------------------------------------------------------------
# Test snip_compact: non-cached removed first
# ---------------------------------------------------------------------------

class TestSnipCompactCacheAware:
    def test_non_cached_truncated_first(self, compressor: ContextCompressor) -> None:
        """snip_compact truncates non-cached oversized results before cached ones."""
        long_content = "x" * 5000
        msgs = [
            _tool_result_msg("t0", long_content),  # index 0: non-cached
            _tool_result_msg("t1", long_content),  # index 1: cached
        ]
        session = _make_session(msgs)
        # Only mark index 1 as cached
        compressor.mark_as_cached({1})

        result = compressor._snip_compact(session)

        # Both should be truncated eventually (second pass)
        for msg in result.messages:
            block = msg.content[0]
            assert isinstance(block, ToolResultBlock)
            assert len(block.content) <= 2000

    def test_cached_also_truncated_as_fallback(self, compressor: ContextCompressor) -> None:
        """snip_compact still truncates cached results when budget requires it."""
        long_content = "y" * 5000
        msgs = [_tool_result_msg("t0", long_content)]
        session = _make_session(msgs)
        # Mark as cached
        compressor.mark_as_cached({0})

        result = compressor._snip_compact(session)
        block = result.messages[0].content[0]
        assert isinstance(block, ToolResultBlock)
        assert len(block.content) <= 2000

    def test_short_cached_results_unchanged(self, compressor: ContextCompressor) -> None:
        """snip_compact does not modify short cached results."""
        short = "short content"
        msgs = [_tool_result_msg("t0", short)]
        session = _make_session(msgs)
        compressor.mark_as_cached({0})

        result = compressor._snip_compact(session)
        block = result.messages[0].content[0]
        assert block.content == short


# ---------------------------------------------------------------------------
# Test micro_compact: non-cached removed first
# ---------------------------------------------------------------------------

class TestMicroCompactCacheAware:
    def test_non_cached_stale_removed_first(self, compressor: ContextCompressor) -> None:
        """micro_compact removes non-cached stale reads first, preserving cached ones."""
        use1, result1 = _read_file_pair("/app/foo.py", "content v1", 1)
        use2, result2 = _read_file_pair("/app/foo.py", "content v2", 2)
        use3, result3 = _read_file_pair("/app/foo.py", "content v3", 3)
        msgs = [use1, result1, use2, result2, use3, result3]
        session = _make_session(msgs)

        # Mark the second pair (indices 2, 3) as cached; first pair (0,1) non-cached
        compressor.mark_as_cached({2, 3})

        result = compressor._micro_compact(session)
        all_blocks = [b for m in result.messages for b in m.content]
        tool_results = [b for b in all_blocks if isinstance(b, ToolResultBlock)]

        # The latest result (v3) must always survive
        contents = [b.content for b in tool_results]
        assert "content v3" in contents

    def test_cached_stale_removed_as_fallback(self, compressor: ContextCompressor) -> None:
        """micro_compact removes cached stale reads when no non-cached option exists."""
        use1, result1 = _read_file_pair("/app/bar.py", "v1", 1)
        use2, result2 = _read_file_pair("/app/bar.py", "v2", 2)
        msgs = [use1, result1, use2, result2]
        session = _make_session(msgs)

        # Mark all as cached — fallback must still remove the stale one
        compressor.mark_as_cached({0, 1, 2, 3})

        result = compressor._micro_compact(session)
        all_blocks = [b for m in result.messages for b in m.content]
        tool_results = [b for b in all_blocks if isinstance(b, ToolResultBlock)]
        assert len(tool_results) == 1
        assert tool_results[0].content == "v2"

    def test_non_cached_stale_removed_cached_latest_kept(self, compressor: ContextCompressor) -> None:
        """When non-cached stale reads exist, only they are removed."""
        use1, result1 = _read_file_pair("/app/baz.py", "v1", 1)
        use2, result2 = _read_file_pair("/app/baz.py", "v2", 2)
        msgs = [use1, result1, use2, result2]
        session = _make_session(msgs)

        # First pair non-cached, second pair cached
        compressor.mark_as_cached({2, 3})

        result = compressor._micro_compact(session)
        all_blocks = [b for m in result.messages for b in m.content]
        tool_results = [b for b in all_blocks if isinstance(b, ToolResultBlock)]

        # Latest (v2) should survive; stale (v1) removed
        assert len(tool_results) == 1
        assert tool_results[0].content == "v2"


# ---------------------------------------------------------------------------
# Test context_collapse: cached messages preserved when possible
# ---------------------------------------------------------------------------

class TestContextCollapseCacheAware:
    def test_cached_old_messages_preserved(self, compressor: ContextCompressor) -> None:
        """context_collapse keeps cached messages from the old section intact."""
        # 3 old messages + 6 recent
        old_cached = _text_msg("user", "cached old message")
        old_non_cached = _text_msg("user", "non-cached old message")
        recent = [_text_msg("user" if i % 2 == 0 else "assistant", f"recent {i}") for i in range(6)]
        msgs = [old_cached, old_non_cached] + recent
        session = _make_session(msgs)

        # Mark first old message as cached
        compressor.mark_as_cached({0})

        result = compressor._context_collapse(session, keep_recent=6)

        # The cached old message should still be present in result
        all_text = " ".join(
            b.text for m in result.messages for b in m.content if isinstance(b, TextBlock)
        )
        assert "cached old message" in all_text

    def test_non_cached_old_messages_collapsed(self, compressor: ContextCompressor) -> None:
        """context_collapse collapses non-cached old messages into summary."""
        tid = "t1"
        old_tool_use = _tool_use_msg(tid, "read_file", {"path": "/app/x.py"})
        old_result = _tool_result_msg(tid, "file content")
        recent = [_text_msg("user" if i % 2 == 0 else "assistant", f"recent {i}") for i in range(6)]
        msgs = [old_tool_use, old_result] + recent
        session = _make_session(msgs)

        # No caching — all old messages are non-cached
        result = compressor._context_collapse(session, keep_recent=6)

        all_blocks = [b for m in result.messages[:-6] for b in m.content]
        tool_uses = [b for b in all_blocks if isinstance(b, ToolUseBlock)]
        assert len(tool_uses) == 0  # collapsed, not preserved

    def test_all_cached_falls_through_to_full_collapse(self, compressor: ContextCompressor) -> None:
        """When all old messages are cached, collapse still happens as fallback."""
        msgs = [_text_msg("user", f"old {i}") for i in range(10)]
        session = _make_session(msgs)
        # Mark all as cached
        compressor.mark_as_cached(set(range(10)))

        result = compressor._context_collapse(session, keep_recent=6)
        # Result should have fewer messages than original or preserved cached messages
        assert len(result.messages) <= len(msgs)


# ---------------------------------------------------------------------------
# Test auto_compact: cached messages preserved in summary section
# ---------------------------------------------------------------------------

class TestAutoCompactCacheAware:
    def test_cached_messages_preserved_before_summary(self, compressor: ContextCompressor) -> None:
        """auto_compact keeps cached messages from old section before summary."""
        msgs = [_text_msg("user" if i % 2 == 0 else "assistant", f"msg {i}") for i in range(10)]
        session = _make_session(msgs)

        # Mark first two messages as cached
        compressor.mark_as_cached({0, 1})

        result = compressor._auto_compact(session, keep_recent=4)

        # Should have: 2 cached + 1 summary + 4 recent = 7 messages
        assert len(result.messages) == 7

        # The first two should be the cached messages
        assert result.messages[0].content[0].text == "msg 0"  # type: ignore[union-attr]
        assert result.messages[1].content[0].text == "msg 1"  # type: ignore[union-attr]

        # Third message should be summary
        third_blocks = result.messages[2].content
        assert any(
            isinstance(b, TextBlock) and "[Previous conversation summary]" in b.text
            for b in third_blocks
        )

    def test_no_cached_messages_standard_behavior(self, compressor: ContextCompressor) -> None:
        """auto_compact without cached messages produces 1 summary + keep_recent."""
        msgs = [_text_msg("user" if i % 2 == 0 else "assistant", f"msg {i}") for i in range(10)]
        session = _make_session(msgs)

        result = compressor._auto_compact(session, keep_recent=4)

        assert len(result.messages) == 5  # 1 summary + 4 recent
        first = result.messages[0]
        text_blocks = [b for b in first.content if isinstance(b, TextBlock)]
        assert any("[Previous conversation summary]" in b.text for b in text_blocks)

    def test_small_session_unchanged(self, compressor: ContextCompressor) -> None:
        """auto_compact with <= keep_recent messages is returned unchanged."""
        msgs = [_text_msg("user", "hi"), _text_msg("assistant", "hello")]
        session = _make_session(msgs)
        compressor.mark_as_cached({0, 1})
        result = compressor._auto_compact(session, keep_recent=4)
        assert result is session


# ---------------------------------------------------------------------------
# Test budget too tight: cached messages removed as last resort
# ---------------------------------------------------------------------------

class TestFallbackRemovesCached:
    def test_snip_removes_cached_when_no_non_cached_oversized(self, compressor: ContextCompressor) -> None:
        """When all oversized results are cached, snip still truncates them."""
        long_content = "z" * 10000
        msgs = [_tool_result_msg("t0", long_content)]
        session = _make_session(msgs)
        compressor.mark_as_cached({0})

        result = compressor._snip_compact(session)
        block = result.messages[0].content[0]
        assert isinstance(block, ToolResultBlock)
        assert len(block.content) <= 2000

    def test_micro_removes_cached_when_all_stale_cached(self, compressor: ContextCompressor) -> None:
        """When all stale reads are cached, micro still removes them."""
        use1, result1 = _read_file_pair("/app/f.py", "v1", 1)
        use2, result2 = _read_file_pair("/app/f.py", "v2", 2)
        msgs = [use1, result1, use2, result2]
        session = _make_session(msgs)
        compressor.mark_as_cached({0, 1, 2, 3})

        result = compressor._micro_compact(session)
        all_blocks = [b for m in result.messages for b in m.content]
        tool_results = [b for b in all_blocks if isinstance(b, ToolResultBlock)]
        assert len(tool_results) == 1
