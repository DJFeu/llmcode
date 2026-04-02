"""Tests for Session, SessionManager, and serialization helpers."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from llm_code.api.types import (
    ImageBlock,
    Message,
    TextBlock,
    TokenUsage,
    ToolResultBlock,
    ToolUseBlock,
)
from llm_code.runtime.session import Session, SessionManager, SessionSummary


# ---------------------------------------------------------------------------
# Session.create
# ---------------------------------------------------------------------------

class TestSessionCreate:
    def test_create_returns_session(self, tmp_path: Path) -> None:
        s = Session.create(tmp_path)
        assert isinstance(s, Session)

    def test_id_is_8_hex_chars(self, tmp_path: Path) -> None:
        s = Session.create(tmp_path)
        assert len(s.id) == 8
        int(s.id, 16)  # must be valid hex

    def test_messages_empty(self, tmp_path: Path) -> None:
        s = Session.create(tmp_path)
        assert s.messages == ()

    def test_project_path(self, tmp_path: Path) -> None:
        s = Session.create(tmp_path)
        assert s.project_path == tmp_path

    def test_created_and_updated_equal_at_creation(self, tmp_path: Path) -> None:
        s = Session.create(tmp_path)
        assert s.created_at == s.updated_at

    def test_total_usage_zero(self, tmp_path: Path) -> None:
        s = Session.create(tmp_path)
        assert s.total_usage == TokenUsage(input_tokens=0, output_tokens=0)


# ---------------------------------------------------------------------------
# Session.add_message — immutability
# ---------------------------------------------------------------------------

class TestSessionAddMessage:
    def test_returns_new_session(self, tmp_path: Path) -> None:
        s = Session.create(tmp_path)
        msg = Message(role="user", content=(TextBlock(text="hi"),))
        s2 = s.add_message(msg)
        assert s2 is not s

    def test_original_unchanged(self, tmp_path: Path) -> None:
        s = Session.create(tmp_path)
        msg = Message(role="user", content=(TextBlock(text="hi"),))
        s.add_message(msg)
        assert s.messages == ()

    def test_new_session_has_message(self, tmp_path: Path) -> None:
        s = Session.create(tmp_path)
        msg = Message(role="user", content=(TextBlock(text="hi"),))
        s2 = s.add_message(msg)
        assert len(s2.messages) == 1
        assert s2.messages[0] == msg

    def test_accumulates_messages(self, tmp_path: Path) -> None:
        s = Session.create(tmp_path)
        m1 = Message(role="user", content=(TextBlock(text="a"),))
        m2 = Message(role="assistant", content=(TextBlock(text="b"),))
        s = s.add_message(m1).add_message(m2)
        assert len(s.messages) == 2


# ---------------------------------------------------------------------------
# Session.update_usage — immutability
# ---------------------------------------------------------------------------

class TestSessionUpdateUsage:
    def test_returns_new_session(self, tmp_path: Path) -> None:
        s = Session.create(tmp_path)
        s2 = s.update_usage(TokenUsage(10, 20))
        assert s2 is not s

    def test_accumulates_usage(self, tmp_path: Path) -> None:
        s = Session.create(tmp_path)
        s = s.update_usage(TokenUsage(10, 20))
        s = s.update_usage(TokenUsage(5, 8))
        assert s.total_usage == TokenUsage(15, 28)

    def test_original_usage_unchanged(self, tmp_path: Path) -> None:
        s = Session.create(tmp_path)
        s.update_usage(TokenUsage(10, 20))
        assert s.total_usage == TokenUsage(0, 0)


# ---------------------------------------------------------------------------
# Session.estimated_tokens
# ---------------------------------------------------------------------------

class TestEstimatedTokens:
    def test_empty_session(self, tmp_path: Path) -> None:
        s = Session.create(tmp_path)
        assert s.estimated_tokens() == 0

    def test_counts_text_blocks(self, tmp_path: Path) -> None:
        s = Session.create(tmp_path)
        # "hello" = 5 chars // 4 = 1
        msg = Message(role="user", content=(TextBlock(text="hello"),))
        s = s.add_message(msg)
        assert s.estimated_tokens() == 5 // 4

    def test_longer_text(self, tmp_path: Path) -> None:
        text = "a" * 400
        s = Session.create(tmp_path)
        msg = Message(role="user", content=(TextBlock(text=text),))
        s = s.add_message(msg)
        assert s.estimated_tokens() == 100


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------

class TestSessionSerialization:
    def _make_rich_session(self, tmp_path: Path) -> Session:
        s = Session.create(tmp_path)
        m1 = Message(
            role="user",
            content=(
                TextBlock(text="hello"),
                ImageBlock(media_type="image/png", data="abc123"),
            ),
        )
        m2 = Message(
            role="assistant",
            content=(
                TextBlock(text="response"),
                ToolUseBlock(id="t1", name="read_file", input={"path": "/a"}),
            ),
        )
        m3 = Message(
            role="user",
            content=(ToolResultBlock(tool_use_id="t1", content="file contents"),),
        )
        return s.add_message(m1).add_message(m2).add_message(m3).update_usage(TokenUsage(50, 30))

    def test_to_dict_round_trip(self, tmp_path: Path) -> None:
        s = self._make_rich_session(tmp_path)
        d = s.to_dict()
        s2 = Session.from_dict(d)
        assert s2.id == s.id
        assert s2.messages == s.messages
        assert s2.total_usage == s.total_usage
        assert s2.project_path == s.project_path

    def test_dict_is_json_serializable(self, tmp_path: Path) -> None:
        s = self._make_rich_session(tmp_path)
        json.dumps(s.to_dict())  # must not raise


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------

class TestSessionManager:
    def test_save_creates_file(self, tmp_path: Path) -> None:
        mgr = SessionManager(tmp_path / "sessions")
        s = Session.create(tmp_path)
        path = mgr.save(s)
        assert path.exists()

    def test_save_load_round_trip(self, tmp_path: Path) -> None:
        mgr = SessionManager(tmp_path / "sessions")
        s = Session.create(tmp_path)
        msg = Message(role="user", content=(TextBlock(text="hello"),))
        s = s.add_message(msg)
        mgr.save(s)
        loaded = mgr.load(s.id)
        assert loaded.id == s.id
        assert loaded.messages == s.messages

    def test_load_missing_raises(self, tmp_path: Path) -> None:
        mgr = SessionManager(tmp_path / "sessions")
        with pytest.raises(FileNotFoundError):
            mgr.load("nonexistent")

    def test_list_sessions(self, tmp_path: Path) -> None:
        mgr = SessionManager(tmp_path / "sessions")
        s1 = Session.create(tmp_path)
        s2 = Session.create(tmp_path)
        mgr.save(s1)
        time.sleep(0.01)
        mgr.save(s2)
        summaries = mgr.list_sessions()
        assert len(summaries) == 2
        # most recent first
        assert summaries[0].id == s2.id

    def test_list_empty(self, tmp_path: Path) -> None:
        mgr = SessionManager(tmp_path / "sessions")
        assert mgr.list_sessions() == []

    def test_summary_type(self, tmp_path: Path) -> None:
        mgr = SessionManager(tmp_path / "sessions")
        s = Session.create(tmp_path)
        mgr.save(s)
        summaries = mgr.list_sessions()
        assert isinstance(summaries[0], SessionSummary)
        assert summaries[0].id == s.id
        assert summaries[0].message_count == 0
