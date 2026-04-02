"""Tests for llm_code.runtime.compaction (Task 22)."""
from __future__ import annotations

from pathlib import Path


from llm_code.api.types import Message, TextBlock
from llm_code.runtime.session import Session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(*texts: str) -> Session:
    session = Session.create(Path("/tmp"))
    for text in texts:
        msg = Message(role="user", content=(TextBlock(text=text),))
        session = session.add_message(msg)
    return session


def _session_texts(session: Session) -> list[str]:
    texts = []
    for msg in session.messages:
        for block in msg.content:
            if isinstance(block, TextBlock):
                texts.append(block.text)
    return texts


# ---------------------------------------------------------------------------
# needs_compaction
# ---------------------------------------------------------------------------

class TestNeedsCompaction:
    def test_false_for_small_session(self):
        from llm_code.runtime.compaction import needs_compaction

        session = _make_session("hello", "world")
        assert needs_compaction(session) is False

    def test_true_for_large_session(self):
        from llm_code.runtime.compaction import needs_compaction

        # Each char = 0.25 tokens, so 80001*4 chars = >80000 tokens
        big_text = "x" * (80001 * 4)
        session = _make_session(big_text)
        assert needs_compaction(session) is True

    def test_custom_threshold(self):
        from llm_code.runtime.compaction import needs_compaction

        # 100 chars = 25 tokens; threshold 10 → True
        session = _make_session("a" * 100)
        assert needs_compaction(session, threshold=10) is True
        assert needs_compaction(session, threshold=100) is False

    def test_exactly_at_threshold_is_false(self):
        from llm_code.runtime.compaction import needs_compaction

        # estimated_tokens = len("x"*400) // 4 = 100; threshold=100 → not >100
        session = _make_session("x" * 400)
        assert needs_compaction(session, threshold=100) is False

    def test_one_above_threshold_is_true(self):
        from llm_code.runtime.compaction import needs_compaction

        # 404 chars // 4 = 101 tokens; threshold=100 → True
        session = _make_session("x" * 404)
        assert needs_compaction(session, threshold=100) is True


# ---------------------------------------------------------------------------
# compact_session
# ---------------------------------------------------------------------------

class TestCompactSession:
    def test_no_op_when_small(self):
        from llm_code.runtime.compaction import compact_session

        session = _make_session("a", "b", "c")
        result = compact_session(session, keep_recent=4)
        assert result is session

    def test_no_op_exact_keep_recent(self):
        from llm_code.runtime.compaction import compact_session

        session = _make_session("a", "b", "c", "d")
        result = compact_session(session, keep_recent=4)
        assert result is session

    def test_compacts_when_exceeds_keep_recent(self):
        from llm_code.runtime.compaction import compact_session

        session = _make_session("a", "b", "c", "d", "e")
        result = compact_session(session, keep_recent=4)
        # 1 summary + 4 recent = 5 messages
        assert len(result.messages) == 5

    def test_preserves_last_n_messages(self):
        from llm_code.runtime.compaction import compact_session

        session = _make_session("a", "b", "c", "d", "e", "f")
        result = compact_session(session, keep_recent=3)
        # Last 3 messages should be d, e, f
        recent = result.messages[-3:]
        texts = [b.text for msg in recent for b in msg.content if isinstance(b, TextBlock)]
        assert texts == ["d", "e", "f"]

    def test_first_message_is_summary(self):
        from llm_code.runtime.compaction import compact_session

        session = _make_session("a", "b", "c", "d", "e")
        result = compact_session(session, keep_recent=2, summary="Prior context summary.")
        first_msg = result.messages[0]
        assert first_msg.role == "user"
        assert len(first_msg.content) == 1
        block = first_msg.content[0]
        assert isinstance(block, TextBlock)
        assert "[Previous conversation summary]" in block.text
        assert "Prior context summary." in block.text

    def test_summary_message_with_empty_summary(self):
        from llm_code.runtime.compaction import compact_session

        session = _make_session("a", "b", "c", "d", "e")
        result = compact_session(session, keep_recent=2, summary="")
        first_msg = result.messages[0]
        block = first_msg.content[0]
        assert isinstance(block, TextBlock)
        assert "[Previous conversation summary]" in block.text

    def test_returns_new_session_object(self):
        from llm_code.runtime.compaction import compact_session

        session = _make_session("a", "b", "c", "d", "e")
        result = compact_session(session, keep_recent=2)
        assert result is not session

    def test_session_id_preserved(self):
        from llm_code.runtime.compaction import compact_session

        session = _make_session("a", "b", "c", "d", "e")
        result = compact_session(session, keep_recent=2)
        assert result.id == session.id

    def test_project_path_preserved(self):
        from llm_code.runtime.compaction import compact_session

        session = _make_session("a", "b", "c", "d", "e")
        result = compact_session(session, keep_recent=2)
        assert result.project_path == session.project_path
