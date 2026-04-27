"""SQLite session state store (v16 M10).

Replaces the per-session JSON checkpoint files at
``~/.llmcode/checkpoints/<session_id>.json`` with a single SQLite
database at ``~/.llmcode/state.db`` (WAL mode). The store keeps three
tables:

* ``sessions`` — one row per session (id, model, project_path, JSON
  blob with the full :class:`Session.to_dict` payload).
* ``turns`` — one row per (session, idx) covering both the user
  message and the assistant reply for a given conversation turn.
* ``tool_calls`` — one row per tool invocation, foreign-keyed to a
  turn so the pager can render tool history alongside replies.

Concurrency story: WAL handles read concurrency, and we set
``busy_timeout=5000`` so two ``llmcode`` processes sharing the same
``state.db`` will retry on lock contention rather than fail. Writes
are serialised via a process-local ``threading.RLock``; SQLite's own
locking handles the cross-process case.

The store is self-contained — no imports from the rest of the
runtime — so the migration command can build a destination DB without
loading any heavy modules. Imports of :class:`Session` happen only in
the helpers that round-trip a Session object.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    model TEXT,
    project_path TEXT,
    payload TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS turns (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    user_message TEXT,
    assistant_message TEXT,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_turns_session ON turns(session_id, idx);

CREATE TABLE IF NOT EXISTS tool_calls (
    id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL,
    args_json TEXT NOT NULL,
    result_json TEXT,
    created_at REAL NOT NULL
);
"""


@dataclass(frozen=True)
class TurnRecord:
    id: str
    session_id: str
    idx: int
    user_message: str | None
    assistant_message: str | None
    created_at: float


@dataclass(frozen=True)
class ToolCallRecord:
    id: str
    turn_id: str
    tool_name: str
    args: dict[str, Any]
    result: Any | None
    created_at: float


class StateDB:
    """Thin wrapper over SQLite WAL with schema bootstrap on open."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    @property
    def path(self) -> Path:
        return self._db_path

    def _ensure_open(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        with self._lock:
            if self._conn is not None:
                return self._conn
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                self._db_path,
                check_same_thread=False,
                isolation_level=None,  # autocommit; explicit BEGIN below
                timeout=5.0,
            )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(_SCHEMA)
            self._conn = conn
            return conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ── sessions ─────────────────────────────────────────────────────

    def upsert_session(
        self,
        session_id: str,
        payload: dict[str, Any],
        model: str | None = None,
        project_path: str | None = None,
    ) -> None:
        """Insert or update a session row.

        The ``payload`` dict is the full :class:`Session.to_dict` shape;
        we serialise it as JSON so the store does not depend on the
        Session schema staying stable.
        """
        if not session_id:
            raise ValueError("session_id is required")
        conn = self._ensure_open()
        now = time.time()
        body = json.dumps(payload, separators=(",", ":"))
        with self._lock:
            existing = conn.execute(
                "SELECT created_at FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            created_at = float(existing[0]) if existing else now
            conn.execute(
                "INSERT OR REPLACE INTO sessions"
                " (id, model, project_path, payload, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, model, project_path, body, created_at, now),
            )

    def load_session(self, session_id: str) -> dict[str, Any] | None:
        conn = self._ensure_open()
        with self._lock:
            row = conn.execute(
                "SELECT payload FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            logger.warning("state_db: corrupt payload for session %s", session_id)
            return None

    def list_sessions(self) -> list[dict[str, Any]]:
        conn = self._ensure_open()
        with self._lock:
            rows = conn.execute(
                "SELECT id, model, project_path, created_at, updated_at"
                " FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
        return [
            {
                "id": r[0],
                "model": r[1],
                "project_path": r[2],
                "created_at": float(r[3]),
                "updated_at": float(r[4]),
            }
            for r in rows
        ]

    def delete_session(self, session_id: str) -> bool:
        conn = self._ensure_open()
        with self._lock:
            cursor = conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            return cursor.rowcount > 0

    # ── turns ────────────────────────────────────────────────────────

    def append_turn(
        self,
        turn_id: str,
        session_id: str,
        idx: int,
        user_message: str | None,
        assistant_message: str | None,
    ) -> None:
        conn = self._ensure_open()
        with self._lock:
            conn.execute(
                "INSERT OR REPLACE INTO turns"
                " (id, session_id, idx, user_message, assistant_message, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (turn_id, session_id, idx, user_message, assistant_message, time.time()),
            )

    def load_turns(self, session_id: str, limit: int | None = None) -> list[TurnRecord]:
        conn = self._ensure_open()
        with self._lock:
            sql = (
                "SELECT id, session_id, idx, user_message, assistant_message, created_at"
                " FROM turns WHERE session_id=? ORDER BY idx ASC"
            )
            params: tuple[Any, ...] = (session_id,)
            if limit is not None:
                sql += " LIMIT ?"
                params = params + (int(limit),)
            rows = conn.execute(sql, params).fetchall()
        return [
            TurnRecord(
                id=r[0],
                session_id=r[1],
                idx=int(r[2]),
                user_message=r[3],
                assistant_message=r[4],
                created_at=float(r[5]),
            )
            for r in rows
        ]

    def load_recent_turns(
        self, session_id: str, count: int = 50
    ) -> list[TurnRecord]:
        """Latest ``count`` turns, oldest first.

        The pager uses this — most-recent-first ordering means we hit
        the index then reverse in Python; cheaper than ORDER BY idx
        ASC + OFFSET for long sessions.
        """
        conn = self._ensure_open()
        with self._lock:
            rows = conn.execute(
                "SELECT id, session_id, idx, user_message, assistant_message, created_at"
                " FROM turns WHERE session_id=? ORDER BY idx DESC LIMIT ?",
                (session_id, int(count)),
            ).fetchall()
        out = [
            TurnRecord(
                id=r[0],
                session_id=r[1],
                idx=int(r[2]),
                user_message=r[3],
                assistant_message=r[4],
                created_at=float(r[5]),
            )
            for r in rows
        ]
        out.reverse()
        return out

    # ── tool calls ───────────────────────────────────────────────────

    def append_tool_call(
        self,
        call_id: str,
        turn_id: str,
        tool_name: str,
        args: dict[str, Any],
        result: Any | None = None,
    ) -> None:
        conn = self._ensure_open()
        body = json.dumps(args, separators=(",", ":"))
        result_body = (
            None if result is None else json.dumps(result, separators=(",", ":"))
        )
        with self._lock:
            conn.execute(
                "INSERT OR REPLACE INTO tool_calls"
                " (id, turn_id, tool_name, args_json, result_json, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (call_id, turn_id, tool_name, body, result_body, time.time()),
            )

    def load_tool_calls(self, turn_id: str) -> list[ToolCallRecord]:
        conn = self._ensure_open()
        with self._lock:
            rows = conn.execute(
                "SELECT id, turn_id, tool_name, args_json, result_json, created_at"
                " FROM tool_calls WHERE turn_id=? ORDER BY created_at ASC",
                (turn_id,),
            ).fetchall()
        out: list[ToolCallRecord] = []
        for r in rows:
            try:
                args = json.loads(r[3])
            except json.JSONDecodeError:
                args = {}
            try:
                result = json.loads(r[4]) if r[4] is not None else None
            except json.JSONDecodeError:
                result = None
            out.append(ToolCallRecord(
                id=r[0],
                turn_id=r[1],
                tool_name=r[2],
                args=args,
                result=result,
                created_at=float(r[5]),
            ))
        return out


# ── module-level helper ───────────────────────────────────────────────


def default_state_db_path() -> Path:
    return Path.home() / ".llmcode" / "state.db"


_GLOBAL_DB: StateDB | None = None


def get_state_db() -> StateDB:
    """Return the process-wide :class:`StateDB` (lazy)."""
    global _GLOBAL_DB
    if _GLOBAL_DB is None:
        _GLOBAL_DB = StateDB(default_state_db_path())
    return _GLOBAL_DB


def set_state_db(db: StateDB | None) -> None:
    """Override the process-wide store; used by tests + migration."""
    global _GLOBAL_DB
    if _GLOBAL_DB is not None and _GLOBAL_DB is not db:
        _GLOBAL_DB.close()
    _GLOBAL_DB = db
