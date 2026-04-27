"""Tests for v16 M10 SQLite state DB.

Covers schema bootstrap, session round trips, turn ordering, tool
call linkage, concurrent writes (the spec R1 mitigation), and the
``llmcode migrate v2.6 state-db`` migration including the
mid-migration crash path.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from llm_code.cli.migrate_v26_state_db import migrate_checkpoints_to_state_db
from llm_code.runtime.state_db import (
    StateDB,
    default_state_db_path,
    get_state_db,
    set_state_db,
)


@pytest.fixture
def db(tmp_path: Path) -> StateDB:
    return StateDB(tmp_path / "state.db")


# ── basic schema + lifecycle ──────────────────────────────────────────


def test_open_creates_schema(db: StateDB, tmp_path: Path) -> None:
    db._ensure_open()
    conn = sqlite3.connect(tmp_path / "state.db")
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    conn.close()
    names = [r[0] for r in rows]
    assert "sessions" in names
    assert "turns" in names
    assert "tool_calls" in names


def test_session_round_trip(db: StateDB) -> None:
    payload = {"id": "s1", "messages": [], "model": "claude-haiku-4"}
    db.upsert_session(
        session_id="s1",
        payload=payload,
        model="claude-haiku-4",
        project_path="/tmp/proj",
    )
    loaded = db.load_session("s1")
    assert loaded is not None
    assert loaded["id"] == "s1"
    assert loaded["model"] == "claude-haiku-4"

    sessions = db.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["id"] == "s1"
    assert sessions[0]["project_path"] == "/tmp/proj"


def test_session_load_missing_returns_none(db: StateDB) -> None:
    assert db.load_session("nope") is None


def test_session_delete(db: StateDB) -> None:
    db.upsert_session("s1", {"id": "s1"})
    assert db.delete_session("s1") is True
    assert db.delete_session("s1") is False
    assert db.load_session("s1") is None


def test_upsert_session_preserves_created_at(db: StateDB) -> None:
    db.upsert_session("s1", {"id": "s1"})
    sessions = db.list_sessions()
    first_created = sessions[0]["created_at"]
    db.upsert_session("s1", {"id": "s1", "tag": "v2"})
    sessions = db.list_sessions()
    assert sessions[0]["created_at"] == first_created


def test_upsert_session_requires_id(db: StateDB) -> None:
    with pytest.raises(ValueError):
        db.upsert_session("", {})


# ── turns ─────────────────────────────────────────────────────────────


def test_turn_round_trip(db: StateDB) -> None:
    db.upsert_session("s1", {"id": "s1"})
    db.append_turn("t1", "s1", 0, "hi", "hello")
    db.append_turn("t2", "s1", 1, "more", "ok")
    turns = db.load_turns("s1")
    assert len(turns) == 2
    assert turns[0].user_message == "hi"
    assert turns[1].assistant_message == "ok"


def test_turn_recent_returns_oldest_first(db: StateDB) -> None:
    db.upsert_session("s1", {"id": "s1"})
    for i in range(10):
        db.append_turn(f"t{i}", "s1", i, f"u{i}", f"a{i}")
    recent = db.load_recent_turns("s1", count=3)
    assert [t.idx for t in recent] == [7, 8, 9]


def test_turn_load_with_limit(db: StateDB) -> None:
    db.upsert_session("s1", {"id": "s1"})
    for i in range(5):
        db.append_turn(f"t{i}", "s1", i, "u", "a")
    assert len(db.load_turns("s1", limit=2)) == 2


# ── tool calls ────────────────────────────────────────────────────────


def test_tool_calls_round_trip(db: StateDB) -> None:
    db.upsert_session("s1", {"id": "s1"})
    db.append_turn("t1", "s1", 0, "hi", "ok")
    db.append_tool_call("c1", "t1", "read_file", {"path": "x"}, {"ok": True})
    db.append_tool_call("c2", "t1", "bash", {"command": "ls"}, "out")
    calls = db.load_tool_calls("t1")
    assert len(calls) == 2
    assert calls[0].tool_name == "read_file"
    assert calls[0].args == {"path": "x"}
    assert calls[0].result == {"ok": True}


def test_tool_calls_corrupt_args_falls_back(db: StateDB, tmp_path: Path) -> None:
    """Defensive path: bad JSON in args_json yields {}."""
    db.upsert_session("s1", {"id": "s1"})
    db.append_turn("t1", "s1", 0, "hi", "ok")
    conn = db._ensure_open()
    conn.execute(
        "INSERT INTO tool_calls (id, turn_id, tool_name, args_json, result_json, created_at)"
        " VALUES ('bad', 't1', 'read_file', 'not-json', NULL, 0)"
    )
    calls = db.load_tool_calls("t1")
    assert any(c.id == "bad" for c in calls)
    bad = next(c for c in calls if c.id == "bad")
    assert bad.args == {}


# ── concurrency ───────────────────────────────────────────────────────


def test_concurrent_writers_serialize(tmp_path: Path) -> None:
    """Two threads writing to the same DB should not corrupt or skip rows."""
    db_path = tmp_path / "state.db"
    db = StateDB(db_path)
    db.upsert_session("s1", {"id": "s1"})

    errors: list[Exception] = []

    def writer(idx_start: int) -> None:
        try:
            local_db = StateDB(db_path)
            for i in range(idx_start, idx_start + 25):
                local_db.append_turn(f"t{i}", "s1", i, f"u{i}", f"a{i}")
            local_db.close()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=writer, args=(0,))
    t2 = threading.Thread(target=writer, args=(25,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert errors == []
    turns = db.load_turns("s1")
    assert len(turns) == 50


def test_busy_timeout_prevents_immediate_failure(tmp_path: Path) -> None:
    """Cross-process simulation: opening a second connection should
    still let writes succeed."""
    db_path = tmp_path / "state.db"
    db1 = StateDB(db_path)
    db1.upsert_session("s1", {"id": "s1"})
    db2 = StateDB(db_path)
    # Hold a read on db1 while db2 writes
    conn1 = db1._ensure_open()
    rows = conn1.execute("SELECT * FROM sessions").fetchall()
    assert len(rows) == 1
    db2.upsert_session("s2", {"id": "s2"})
    sessions = db1.list_sessions()
    assert len(sessions) == 2


# ── module-level singleton ────────────────────────────────────────────


def test_default_state_db_path_under_home() -> None:
    p = default_state_db_path()
    assert p.name == "state.db"
    assert ".llmcode" in str(p)


def test_set_state_db_replaces_singleton(tmp_path: Path) -> None:
    custom = StateDB(tmp_path / "alt.db")
    set_state_db(custom)
    try:
        assert get_state_db() is custom
    finally:
        set_state_db(None)


# ── migration ─────────────────────────────────────────────────────────


def _write_checkpoint(dir_: Path, sid: str, body: dict) -> Path:
    p = dir_ / f"{sid}.json"
    p.write_text(json.dumps(body), encoding="utf-8")
    return p


def test_migration_round_trip(tmp_path: Path) -> None:
    src = tmp_path / "checkpoints"
    src.mkdir()
    for i in range(5):
        _write_checkpoint(src, f"sess{i}", {
            "id": f"sess{i}",
            "messages": [],
            "project_path": str(tmp_path),
        })
    dst = tmp_path / "state.db"
    backup_root = tmp_path / "checkpoints.bak"
    summary = migrate_checkpoints_to_state_db(src, dst, backup_root)
    assert summary["migrated"] == 5
    assert summary["skipped"] == 0
    assert dst.exists()
    db = StateDB(dst)
    sessions = db.list_sessions()
    assert {s["id"] for s in sessions} == {f"sess{i}" for i in range(5)}
    # Originals moved to backup
    assert list(src.glob("*.json")) == []
    assert summary["backup"] is not None
    backup_dir = Path(summary["backup"])
    assert {p.name for p in backup_dir.glob("*.json")} == {
        f"sess{i}.json" for i in range(5)
    }


def test_migration_skips_corrupt_files(tmp_path: Path) -> None:
    src = tmp_path / "checkpoints"
    src.mkdir()
    _write_checkpoint(src, "good", {"id": "good"})
    (src / "bad.json").write_text("not-json", encoding="utf-8")
    dst = tmp_path / "state.db"
    backup_root = tmp_path / "checkpoints.bak"
    summary = migrate_checkpoints_to_state_db(src, dst, backup_root)
    assert summary["migrated"] == 1
    assert summary["skipped"] == 1


def test_migration_atomic_on_failure(tmp_path: Path, monkeypatch) -> None:
    """If a write blows up mid-migration, the destination DB must not exist."""
    src = tmp_path / "checkpoints"
    src.mkdir()
    _write_checkpoint(src, "sess1", {"id": "sess1"})
    dst = tmp_path / "state.db"
    backup_root = tmp_path / "checkpoints.bak"

    real_upsert = StateDB.upsert_session
    calls = {"n": 0}

    def boom(self, *args, **kwargs):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] >= 1:
            raise RuntimeError("disk full")
        return real_upsert(self, *args, **kwargs)

    monkeypatch.setattr(StateDB, "upsert_session", boom)

    with pytest.raises(RuntimeError):
        migrate_checkpoints_to_state_db(src, dst, backup_root)

    assert not dst.exists()
    # Originals untouched
    assert (src / "sess1.json").exists()


def test_migration_handles_empty_checkpoint_dir(tmp_path: Path) -> None:
    summary = migrate_checkpoints_to_state_db(
        tmp_path / "checkpoints", tmp_path / "state.db", tmp_path / "bak"
    )
    assert summary == {"migrated": 0, "skipped": 0, "backup": None}
