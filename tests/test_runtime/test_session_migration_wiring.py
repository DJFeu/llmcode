"""Wiring tests: all session resume paths route through load_and_migrate."""
from __future__ import annotations

import json
from pathlib import Path

from llm_code.runtime.session import SessionManager, Session
from llm_code.runtime.checkpoint_recovery import CheckpointRecovery


def _write_v1_session(path: Path, session_id: str = "legacy1") -> None:
    """Write a v1-shaped session: bare list OR dict without _schema_version,
    with legacy 'tool_calls' on an assistant message (v2 style) and an
    orphan thinking-only message."""
    data = {
        "id": session_id,
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            # Orphan thinking — should be filtered
            {"role": "assistant", "content": [{"type": "thinking", "thinking": "..."}]},
            # Legacy tool_calls field (v2 → v3 migration)
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "calling tool"}],
                "tool_calls": [
                    {"id": "tu1", "name": "read_file", "input": {"path": "x"}}
                ],
            },
            # Matching tool_result so the chain is valid
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu1", "content": "ok"}
                ],
            },
        ],
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "total_usage": {"input_tokens": 0, "output_tokens": 0},
        "project_path": "/tmp",
    }
    path.write_text(json.dumps(data), encoding="utf-8")


def test_session_manager_load_migrates_legacy(tmp_path):
    mgr = SessionManager(tmp_path)
    _write_v1_session(tmp_path / "legacy1.json")

    session = mgr.load("legacy1")
    # Orphan thinking filtered → fewer messages than written (4 → 3)
    assert len(session.messages) == 3
    # Legacy tool_calls migrated into content as tool_use block
    assistant = session.messages[1]
    assert assistant.role == "assistant"
    assert any(
        getattr(b, "__class__", type(b)).__name__ == "ToolUseBlock"
        for b in assistant.content
    )


def test_checkpoint_recovery_load_migrates_legacy(tmp_path):
    rec = CheckpointRecovery(tmp_path)
    _write_v1_session(tmp_path / "legacy1.json", session_id="legacy1")

    session = rec.load_checkpoint("legacy1")
    assert session is not None
    assert len(session.messages) == 3


def test_session_writer_emits_schema_version(tmp_path):
    mgr = SessionManager(tmp_path)
    session = Session.create(project_path=tmp_path)
    path = mgr.save(session)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data.get("_schema_version") == 3
