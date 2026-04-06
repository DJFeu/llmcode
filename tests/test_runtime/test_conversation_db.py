"""Tests for SQLite conversation database."""
import pytest
from pathlib import Path
from llm_code.runtime.conversation_db import ConversationDB, SearchResult, UsageSummary


@pytest.fixture
def db(tmp_path):
    """Create a temporary ConversationDB."""
    db = ConversationDB(db_path=tmp_path / "test.db")
    yield db
    db.close()


class TestConversationDB:
    def test_ensure_conversation(self, db):
        db.ensure_conversation("conv-1", name="Test", model="qwen", project_path="/tmp")
        convs = db.list_conversations()
        assert len(convs) == 1
        assert convs[0]["id"] == "conv-1"

    def test_ensure_conversation_idempotent(self, db):
        db.ensure_conversation("conv-1", name="Test")
        db.ensure_conversation("conv-1", name="Test2")  # should not fail
        convs = db.list_conversations()
        assert len(convs) == 1

    def test_ensure_conversation_preserves_original_name(self, db):
        db.ensure_conversation("conv-1", name="Original")
        db.ensure_conversation("conv-1", name="Changed")
        convs = db.list_conversations()
        assert convs[0]["name"] == "Original"

    def test_log_message(self, db):
        db.ensure_conversation("conv-1")
        db.log_message("conv-1", "user", "hello world")
        db.log_message("conv-1", "assistant", "hi there")
        results = db.search("hello")
        assert len(results) == 1
        assert results[0].role == "user"

    def test_search_fts(self, db):
        db.ensure_conversation("conv-1", name="Debug session")
        db.log_message("conv-1", "user", "fix the authentication bug")
        db.log_message("conv-1", "assistant", "I found the issue in auth.py")

        results = db.search("authentication")
        assert len(results) >= 1
        assert "conv-1" in [r.conversation_id for r in results]

        results = db.search("nonexistent_term_xyz")
        assert len(results) == 0

    def test_search_returns_search_result(self, db):
        db.ensure_conversation("conv-1", name="Test", project_path="/proj")
        db.log_message("conv-1", "user", "unique_marker_text")
        results = db.search("unique_marker_text")
        assert len(results) == 1
        r = results[0]
        assert isinstance(r, SearchResult)
        assert r.conversation_name == "Test"
        assert r.project_path == "/proj"

    def test_search_limit(self, db):
        db.ensure_conversation("conv-1")
        for i in range(10):
            db.log_message("conv-1", "user", f"searchable item number {i}")
        results = db.search("searchable", limit=3)
        assert len(results) == 3

    def test_search_across_conversations(self, db):
        db.ensure_conversation("conv-1", name="First")
        db.ensure_conversation("conv-2", name="Second")
        db.log_message("conv-1", "user", "common keyword here")
        db.log_message("conv-2", "user", "common keyword there")
        results = db.search("common keyword")
        assert len(results) == 2
        conv_ids = {r.conversation_id for r in results}
        assert conv_ids == {"conv-1", "conv-2"}

    def test_usage_summary(self, db):
        db.ensure_conversation("conv-1")
        db.log_message("conv-1", "user", "q1", input_tokens=100, output_tokens=0)
        db.log_message("conv-1", "assistant", "a1", input_tokens=0, output_tokens=200)
        db.log_message("conv-1", "user", "q2", input_tokens=150, output_tokens=0)

        summary = db.usage_summary()
        assert isinstance(summary, UsageSummary)
        assert summary.total_input_tokens == 250
        assert summary.total_output_tokens == 200
        assert summary.total_messages == 3
        assert summary.conversations == 1
        assert summary.since is None

    def test_usage_summary_multiple_conversations(self, db):
        db.ensure_conversation("conv-1")
        db.ensure_conversation("conv-2")
        db.log_message("conv-1", "user", "q1", input_tokens=100)
        db.log_message("conv-2", "user", "q2", input_tokens=200)

        summary = db.usage_summary()
        assert summary.total_input_tokens == 300
        assert summary.conversations == 2

    def test_usage_summary_since_days(self, db):
        db.ensure_conversation("conv-1")
        db.log_message(
            "conv-1", "user", "recent", input_tokens=100,
            created_at="2099-01-01T00:00:00",
        )
        summary = db.usage_summary(since_days=7)
        assert isinstance(summary, UsageSummary)
        assert summary.since == "7 days ago"

    def test_list_conversations(self, db):
        db.ensure_conversation("conv-1", name="First")
        db.ensure_conversation("conv-2", name="Second")
        convs = db.list_conversations()
        assert len(convs) == 2

    def test_list_conversations_limit(self, db):
        for i in range(5):
            db.ensure_conversation(f"conv-{i}", name=f"Conv {i}")
        convs = db.list_conversations(limit=3)
        assert len(convs) == 3

    def test_list_conversations_returns_all_fields(self, db):
        db.ensure_conversation(
            "conv-1", name="Test", model="qwen",
            project_path="/proj", created_at="2025-01-01",
        )
        convs = db.list_conversations()
        assert convs[0]["id"] == "conv-1"
        assert convs[0]["name"] == "Test"
        assert convs[0]["model"] == "qwen"
        assert convs[0]["project_path"] == "/proj"
        assert convs[0]["created_at"] == "2025-01-01"

    def test_empty_search(self, db):
        results = db.search("anything")
        assert results == []

    def test_empty_usage(self, db):
        summary = db.usage_summary()
        assert summary.total_input_tokens == 0
        assert summary.total_output_tokens == 0
        assert summary.total_messages == 0
        assert summary.conversations == 0

    def test_frozen_dataclasses(self):
        sr = SearchResult(
            conversation_id="c1", conversation_name="n",
            project_path="/p", role="user",
            content_snippet="s", created_at="t",
        )
        with pytest.raises(AttributeError):
            sr.role = "assistant"  # type: ignore[misc]

        us = UsageSummary(
            total_input_tokens=0, total_output_tokens=0,
            total_messages=0, conversations=0, since=None,
        )
        with pytest.raises(AttributeError):
            us.total_input_tokens = 1  # type: ignore[misc]

    def test_db_file_created(self, tmp_path):
        db_path = tmp_path / "subdir" / "test.db"
        db = ConversationDB(db_path=db_path)
        assert db_path.exists()
        db.close()

    def test_close_and_reopen(self, tmp_path):
        db_path = tmp_path / "test.db"
        db = ConversationDB(db_path=db_path)
        db.ensure_conversation("conv-1", name="Persistent")
        db.log_message("conv-1", "user", "remember me")
        db.close()

        db2 = ConversationDB(db_path=db_path)
        convs = db2.list_conversations()
        assert len(convs) == 1
        assert convs[0]["name"] == "Persistent"
        results = db2.search("remember")
        assert len(results) == 1
        db2.close()
