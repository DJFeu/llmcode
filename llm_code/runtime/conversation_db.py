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
    created_at TEXT
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
        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        """Create tables if they don't exist."""
        try:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        except sqlite3.Error:
            logger.exception("Failed to initialize conversation database schema")

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
    ) -> None:
        """Log a single message. Call after each message completes."""
        try:
            self._conn.execute(
                "INSERT INTO messages (conversation_id, role, content, input_tokens, output_tokens, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (conversation_id, role, content, input_tokens, output_tokens, created_at),
            )
            self._conn.commit()
        except sqlite3.Error:
            logger.exception("Failed to log message for conversation %s", conversation_id)

    def search(self, query: str, limit: int = 20) -> list[SearchResult]:
        """Full-text search across all conversations."""
        try:
            rows = self._conn.execute(
                """
                SELECT m.conversation_id, c.name, c.project_path, m.role,
                       snippet(messages_fts, 0, '>>>', '<<<', '...', 40) AS snippet,
                       m.created_at
                FROM messages_fts
                JOIN messages m ON messages_fts.rowid = m.id
                JOIN conversations c ON m.conversation_id = c.id
                WHERE messages_fts MATCH ?
                ORDER BY m.created_at DESC
                LIMIT ?
                """,
                (query, limit),
            ).fetchall()
            return [
                SearchResult(
                    conversation_id=r["conversation_id"],
                    conversation_name=r["name"] or "",
                    project_path=r["project_path"] or "",
                    role=r["role"],
                    content_snippet=r["snippet"],
                    created_at=r["created_at"] or "",
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
