"""Wave2-1a P5: conversation_db thinking persistence + FTS5.

Five responsibilities pinned here:

1. Schema migration for pre-P5 databases — open an existing DB file
   missing the new columns, verify ALTER TABLE adds them without
   data loss and is re-runnable.
2. ``log_message`` back-compat — existing callers keep working with
   content_type defaulted to "text".
3. ``log_thinking`` convenience wrapper stores content + signature
   with role="assistant" and content_type="thinking".
4. FTS5 search with ``content_type`` filter returns only thinking,
   only text, or both.
5. Signature bytes round-trip byte-for-byte through the DB.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from llm_code.runtime.conversation_db import ConversationDB


@pytest.fixture
def db(tmp_path: Path) -> ConversationDB:
    conv_db = ConversationDB(db_path=tmp_path / "test.db")
    conv_db.ensure_conversation(
        conv_id="conv-1",
        name="test",
        model="test-model",
        project_path="/tmp/test",
        created_at="2026-04-09T00:00:00Z",
    )
    return conv_db


# ---------- Schema migration ----------

def test_migration_adds_content_type_and_signature_to_fresh_db(tmp_path: Path) -> None:
    """A fresh DB built via the new schema has both columns present."""
    ConversationDB(db_path=tmp_path / "fresh.db").close()
    conn = sqlite3.connect(str(tmp_path / "fresh.db"))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
    conn.close()
    assert "content_type" in cols
    assert "signature" in cols


def test_migration_adds_columns_to_legacy_db(tmp_path: Path) -> None:
    """Simulate a pre-P5 DB: create the old schema manually with no
    content_type / signature columns, insert a row, then open it via
    ConversationDB. The migration must add both columns and the
    pre-existing row must still be readable with content_type
    defaulting to 'text'."""
    legacy_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(legacy_path))
    conn.executescript(
        """
        CREATE TABLE conversations (
            id TEXT PRIMARY KEY,
            name TEXT,
            model TEXT,
            project_path TEXT,
            created_at TEXT
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT,
            role TEXT NOT NULL,
            content TEXT,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            created_at TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO conversations (id, name, created_at) VALUES ('legacy', 'old', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at) "
        "VALUES ('legacy', 'user', 'pre-p5 message', '2026-01-01')"
    )
    conn.commit()
    conn.close()

    # Open via ConversationDB — this should migrate
    db = ConversationDB(db_path=legacy_path)
    try:
        conn = db._conn
        cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
        assert "content_type" in cols
        assert "signature" in cols
        # Legacy row is still readable with content_type defaulted
        row = conn.execute(
            "SELECT content, COALESCE(content_type, 'text') AS ct FROM messages WHERE role = 'user'"
        ).fetchone()
        assert row["content"] == "pre-p5 message"
        assert row["ct"] == "text"
    finally:
        db.close()


def test_migration_is_idempotent(tmp_path: Path) -> None:
    """Opening a DB that already has the new columns must not raise."""
    db_path = tmp_path / "idempotent.db"
    ConversationDB(db_path=db_path).close()
    # Second open — migration should no-op silently
    db2 = ConversationDB(db_path=db_path)
    db2.close()


# ---------- log_message back-compat ----------

def test_log_message_defaults_content_type_to_text(db: ConversationDB) -> None:
    """Every pre-P5 caller of log_message must keep working with the
    default content_type = 'text'."""
    db.log_message(
        conversation_id="conv-1",
        role="user",
        content="plain message",
        created_at="2026-04-09T00:00:01Z",
    )
    row = db._conn.execute(
        "SELECT content_type, signature FROM messages WHERE content = 'plain message'"
    ).fetchone()
    assert row["content_type"] == "text"
    assert row["signature"] == ""


# ---------- log_thinking ----------

def test_log_thinking_stores_signature_and_type(db: ConversationDB) -> None:
    db.log_thinking(
        conversation_id="conv-1",
        content="step 1: consider the options",
        signature="opaque-sig",
        created_at="2026-04-09T00:00:02Z",
    )
    row = db._conn.execute(
        "SELECT role, content, content_type, signature FROM messages WHERE content_type = 'thinking'"
    ).fetchone()
    assert row["role"] == "assistant"
    assert row["content"] == "step 1: consider the options"
    assert row["content_type"] == "thinking"
    assert row["signature"] == "opaque-sig"


def test_log_thinking_preserves_signature_bytes(db: ConversationDB) -> None:
    """Anthropic signs thinking with base64 that can contain unicode
    and trailing whitespace. The DB round-trip must be byte-exact."""
    tricky = "abc+/==\n  \u00e9\u00a0tail"
    db.log_thinking(
        conversation_id="conv-1",
        content="x",
        signature=tricky,
        created_at="2026-04-09T00:00:03Z",
    )
    row = db._conn.execute(
        "SELECT signature FROM messages WHERE content_type = 'thinking'"
    ).fetchone()
    assert row["signature"] == tricky
    assert len(row["signature"]) == len(tricky)


# ---------- FTS5 search by content_type ----------

def test_search_without_filter_finds_both_types(db: ConversationDB) -> None:
    db.log_message(
        conversation_id="conv-1",
        role="assistant",
        content="visible answer mentioning wavelength",
        created_at="2026-04-09T00:00:04Z",
    )
    db.log_thinking(
        conversation_id="conv-1",
        content="reasoning about wavelength properties",
        created_at="2026-04-09T00:00:05Z",
    )
    results = db.search("wavelength")
    types = {r.content_type for r in results}
    assert types == {"text", "thinking"}


def test_search_filter_thinking_only(db: ConversationDB) -> None:
    db.log_message(
        conversation_id="conv-1",
        role="assistant",
        content="text answer with keyword zxcvbn",
        created_at="2026-04-09T00:00:06Z",
    )
    db.log_thinking(
        conversation_id="conv-1",
        content="reasoning with keyword zxcvbn",
        created_at="2026-04-09T00:00:07Z",
    )
    results = db.search("zxcvbn", content_type="thinking")
    assert len(results) == 1
    assert results[0].content_type == "thinking"


def test_search_filter_text_only_excludes_thinking(db: ConversationDB) -> None:
    db.log_message(
        conversation_id="conv-1",
        role="assistant",
        content="visible text with keyword plumbus",
        created_at="2026-04-09T00:00:08Z",
    )
    db.log_thinking(
        conversation_id="conv-1",
        content="reasoning with keyword plumbus",
        created_at="2026-04-09T00:00:09Z",
    )
    results = db.search("plumbus", content_type="text")
    assert len(results) == 1
    assert results[0].content_type == "text"


def test_search_result_carries_content_type_field(db: ConversationDB) -> None:
    """The SearchResult dataclass must expose content_type so UI can
    render thinking matches differently from text matches."""
    db.log_message(
        conversation_id="conv-1",
        role="user",
        content="unique_word_xyz in user message",
        created_at="2026-04-09T00:00:10Z",
    )
    results = db.search("unique_word_xyz")
    assert len(results) == 1
    assert hasattr(results[0], "content_type")
    assert results[0].content_type == "text"


def test_legacy_rows_search_as_text_after_migration(tmp_path: Path) -> None:
    """Rows written before the migration (with NULL content_type)
    must still match text-filtered searches via the COALESCE in the
    WHERE clause."""
    legacy_path = tmp_path / "legacy_search.db"
    conn = sqlite3.connect(str(legacy_path))
    conn.executescript(
        """
        CREATE TABLE conversations (
            id TEXT PRIMARY KEY, name TEXT, model TEXT,
            project_path TEXT, created_at TEXT
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT,
            role TEXT NOT NULL,
            content TEXT,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            created_at TEXT
        );
        CREATE VIRTUAL TABLE messages_fts USING fts5(
            content, content=messages, content_rowid=id
        );
        CREATE TRIGGER messages_fts_insert AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
        END;
        """
    )
    conn.execute("INSERT INTO conversations VALUES ('l', 'x', '', '', '2026')")
    conn.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at) "
        "VALUES ('l', 'user', 'legacy_keyword_qrs', '2026')"
    )
    conn.commit()
    conn.close()

    db = ConversationDB(db_path=legacy_path)
    try:
        # Legacy row has NULL content_type — COALESCE should treat it
        # as 'text' so the text filter still finds it.
        results = db.search("legacy_keyword_qrs", content_type="text")
        assert len(results) == 1
        assert results[0].content_type == "text"
    finally:
        db.close()
