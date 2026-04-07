"""Tests for session schema migration and tolerant loading."""
from __future__ import annotations

import json
from pathlib import Path

from llm_code.runtime.session_migration import (
    SCHEMA_VERSION,
    SCHEMA_VERSION_KEY,
    filter_orphan_thinking,
    load_and_migrate,
    migrate_message,
    rebuild_tool_chain,
)


def test_migrate_v1_attachments_to_images():
    msg = {"role": "user", "content": [], "attachments": [{"data": "x"}]}
    out = migrate_message(msg, from_version=1)
    assert "images" in out
    assert "attachments" not in out


def test_migrate_v2_tool_calls_inlined():
    msg = {
        "role": "assistant",
        "content": [{"type": "text", "text": "hi"}],
        "tool_calls": [{"id": "t1", "name": "Read", "input": {"path": "a"}}],
    }
    out = migrate_message(msg, from_version=2)
    assert "tool_calls" not in out
    assert any(b.get("type") == "tool_use" and b.get("id") == "t1" for b in out["content"])


def test_filter_orphan_thinking():
    msgs = [
        {"role": "assistant", "content": [{"type": "thinking", "text": "..."}]},
        {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
    ]
    out = filter_orphan_thinking(msgs)
    assert len(out) == 1
    assert out[0]["content"][0]["type"] == "text"


def test_rebuild_tool_chain_drops_orphan_use():
    msgs = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "X", "input": {}}]},
        {"role": "assistant", "content": [{"type": "text", "text": "no result followed"}]},
    ]
    out = rebuild_tool_chain(msgs)
    assert all(
        b.get("type") != "tool_use"
        for m in out
        for b in m["content"]
    )


def test_rebuild_tool_chain_keeps_matched_pair():
    msgs = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "X", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
    ]
    out = rebuild_tool_chain(msgs)
    flat = [b for m in out for b in m["content"]]
    assert any(b.get("type") == "tool_use" for b in flat)
    assert any(b.get("type") == "tool_result" for b in flat)


def test_load_and_migrate_v1_list_format(tmp_path: Path):
    p = tmp_path / "session.json"
    p.write_text(json.dumps([
        {"role": "user", "content": [{"type": "text", "text": "hi"}], "attachments": []},
    ]))
    out = load_and_migrate(p)
    assert len(out) == 1
    assert "attachments" not in out[0]


def test_load_and_migrate_unknown_fields_tolerated(tmp_path: Path):
    p = tmp_path / "session.json"
    p.write_text(json.dumps({
        SCHEMA_VERSION_KEY: SCHEMA_VERSION,
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": "hi"}],
                "future_field": "ignored",
            }
        ],
    }))
    out = load_and_migrate(p)
    assert "future_field" not in out[0]
    assert out[0]["role"] == "user"


def test_load_and_migrate_current_schema(tmp_path: Path):
    p = tmp_path / "session.json"
    p.write_text(json.dumps({
        SCHEMA_VERSION_KEY: SCHEMA_VERSION,
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
    }))
    out = load_and_migrate(p)
    assert out == [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
