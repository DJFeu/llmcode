"""SQLite conversation logging with FTS5 search."""
from __future__ import annotations

import sqlite3
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchResult:
    conversation_id: str
    conversation_name: str
    project_path: str
    role: str
    content_snippet: str
    created_at: str
    # Wave2-1a P5: which kind of block produced this match. "text"
    # for ordinary messages (the vast majority, including every
    # pre-P5 row after migration), "thinking" for reasoning traces.
    # Callers can filter by type to build a thinking-only search UI
    # or mix both in a unified timeline.
    content_type: str = "text"


@dataclass(frozen=True)
class UsageSummary:
    total_input_tokens: int
    total_output_tokens: int
    total_messages: int
    conversations: int
    since: str | None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    name TEXT,
    model TEXT,
    project_path TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT REFERENCES conversations(id),
    role TEXT NOT NULL,
    content TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    created_at TEXT,
    -- Wave2-1a P5: content_type distinguishes "text" from "thinking"
    -- rows so FTS5 search can filter by kind. Defaults to "text" so
    -- the ALTER TABLE migration below lights up existing rows
    -- without any data rewrite. signature holds the opaque bytes
    -- Anthropic uses to verify thinking block round-trips.
    content_type TEXT DEFAULT 'text',
    signature TEXT DEFAULT ''
);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
USING fts5(content, content=messages, content_rowid=id);

CREATE TRIGGER IF NOT EXISTS messages_fts_insert
AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_delete
AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
"""


class ConversationDB:
    """Minimal SQLite conversation store with FTS5 search."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._path = db_path or (Path.home() / ".llmcode" / "conversations.db")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._path),
            timeout=10,          # wait up to 10s for locks (multi-instance)
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        # Enable WAL mode for concurrent readers + writer. This allows
        # multiple llmcode instances to share the same DB without
        # "unable to open database file" / "database is locked" errors.
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            pass
        self._init_schema()

    def _init_schema(self) -> None:
        """Create tables if they don't exist."""
        try:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        except sqlite3.Error:
            logger.exception("Failed to initialize conversation database schema")
        # Wave2-1a P5: forward-compat migration for pre-P5 DBs. The
        # CREATE TABLE block above is idempotent and does not touch
        # existing tables, so ALTER TABLE is needed to add the new
        # columns to an older database file. SQLite's IF NOT EXISTS
        # syntax for ADD COLUMN is not supported until 3.35; we work
        # around that by reading PRAGMA table_info and only issuing
        # the ALTER when the column is absent. This keeps the
        # migration safely re-runnable on every startup.
        self._migrate_add_column("messages", "content_type", "TEXT DEFAULT 'text'")
        self._migrate_add_column("messages", "signature", "TEXT DEFAULT ''")

    def _migrate_add_column(self, table: str, column: str, type_def: str) -> None:
        """Add *column* to *table* if it does not already exist."""
        try:
            existing = {
                r["name"]
                for r in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if column in existing:
                return
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {type_def}")
            self._conn.commit()
            logger.info("Migrated %s: added column %s", table, column)
        except sqlite3.Error:
            logger.exception("Failed to add column %s to %s", column, table)

    def ensure_conversation(
        self,
        conv_id: str,
        name: str = "",
        model: str = "",
        project_path: str = "",
        created_at: str = "",
    ) -> None:
        """Insert conversation row if it doesn't exist."""
        try:
            self._conn.execute(
                "INSERT OR IGNORE INTO conversations (id, name, model, project_path, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (conv_id, name, model, project_path, created_at),
            )
            self._conn.commit()
        except sqlite3.Error:
            logger.exception("Failed to ensure conversation %s", conv_id)

    def log_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        created_at: str = "",
        *,
        content_type: str = "text",
        signature: str = "",
    ) -> None:
        """Log a single message. Call after each message completes.

        Wave2-1a P5: ``content_type`` defaults to "text" so every
        existing caller keeps working unchanged. Pass
        ``content_type="thinking"`` (and optionally ``signature``) to
        log a reasoning trace as a searchable row.
        """
        try:
            self._conn.execute(
                "INSERT INTO messages "
                "(conversation_id, role, content, input_tokens, "
                "output_tokens, created_at, content_type, signature) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    conversation_id, role, content, input_tokens,
                    output_tokens, created_at, content_type, signature,
                ),
            )
            self._conn.commit()
        except sqlite3.Error:
            logger.exception("Failed to log message for conversation %s", conversation_id)

    def log_thinking(
        self,
        conversation_id: str,
        content: str,
        signature: str = "",
        created_at: str = "",
    ) -> None:
        """Log an assistant thinking block as a searchable row.

        Convenience wrapper over ``log_message`` that pins role to
        "assistant" and content_type to "thinking". Used by the
        runtime's _db_log path so a reasoning trace is indexed in
        FTS5 alongside visible assistant text.
        """
        self.log_message(
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            created_at=created_at,
            content_type="thinking",
            signature=signature,
        )

    def search(
        self,
        query: str,
        limit: int = 20,
        *,
        content_type: str | None = None,
    ) -> list[SearchResult]:
        """Full-text search across all conversations.

        Wave2-1a P5: optional ``content_type`` filter picks "text"
        only, "thinking" only, or both (None). Pre-P5 rows have
        content_type == 'text' after migration.
        """
        try:
            sql = """
                SELECT m.conversation_id, c.name, c.project_path, m.role,
                       snippet(messages_fts, 0, '>>>', '<<<', '...', 40) AS snippet,
                       m.created_at,
                       COALESCE(m.content_type, 'text') AS content_type
                FROM messages_fts
                JOIN messages m ON messages_fts.rowid = m.id
                JOIN conversations c ON m.conversation_id = c.id
                WHERE messages_fts MATCH ?
            """
            params: list = [query]
            if content_type is not None:
                sql += " AND COALESCE(m.content_type, 'text') = ?"
                params.append(content_type)
            sql += " ORDER BY m.created_at DESC LIMIT ?"
            params.append(limit)

            rows = self._conn.execute(sql, params).fetchall()
            return [
                SearchResult(
                    conversation_id=r["conversation_id"],
                    conversation_name=r["name"] or "",
                    project_path=r["project_path"] or "",
                    role=r["role"],
                    content_snippet=r["snippet"],
                    created_at=r["created_at"] or "",
                    content_type=r["content_type"] or "text",
                )
                for r in rows
            ]
        except sqlite3.Error:
            logger.exception("Search failed for query: %s", query)
            return []

    def usage_summary(self, since_days: int | None = None) -> UsageSummary:
        """Aggregate token usage, optionally filtered by days."""
        where = ""
        params: list = []
        since_str: str | None = None
        if since_days is not None:
            where = "WHERE m.created_at >= datetime('now', ?)"
            params = [f"-{since_days} days"]
            since_str = f"{since_days} days ago"

        try:
            row = self._conn.execute(
                f"""
                SELECT
                    COALESCE(SUM(m.input_tokens), 0) AS total_in,
                    COALESCE(SUM(m.output_tokens), 0) AS total_out,
                    COUNT(*) AS total_msgs,
                    COUNT(DISTINCT m.conversation_id) AS convs
                FROM messages m
                {where}
                """,
                params,
            ).fetchone()

            return UsageSummary(
                total_input_tokens=row["total_in"],
                total_output_tokens=row["total_out"],
                total_messages=row["total_msgs"],
                conversations=row["convs"],
                since=since_str,
            )
        except sqlite3.Error:
            logger.exception("Failed to compute usage summary")
            return UsageSummary(
                total_input_tokens=0,
                total_output_tokens=0,
                total_messages=0,
                conversations=0,
                since=since_str,
            )

    def list_conversations(self, limit: int = 20) -> list[dict]:
        """List recent conversations."""
        try:
            rows = self._conn.execute(
                "SELECT * FROM conversations ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            logger.exception("Failed to list conversations")
            return []

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
