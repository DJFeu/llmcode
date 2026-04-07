"""Tests for created_at preservation and legacy backfill."""
from __future__ import annotations

import os
import time
from datetime import date, datetime, timezone
from pathlib import Path

from llm_code.runtime.memory_layers import distill_daily
from llm_code.runtime.memory_taxonomy import (
    MemoryType,
    TypedMemoryEntry,
    TypedMemoryStore,
)


def _make_store(tmp_path: Path) -> TypedMemoryStore:
    return TypedMemoryStore(tmp_path / "mem")


def test_new_entry_gets_created_at(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    entry = store.create(
        slug="alpha",
        name="Alpha",
        description="d",
        memory_type=MemoryType.PROJECT,
        content="hello world",
    )
    assert entry.created_at
    # Parses as ISO
    dt = datetime.fromisoformat(entry.created_at)
    assert dt.tzinfo is not None


def test_legacy_entry_backfills_from_mtime(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    topic_path = tmp_path / "mem" / "topics" / "legacy.md"
    topic_path.write_text(
        "---\n"
        "name: Legacy\n"
        "description: no created_at field\n"
        "type: user\n"
        "---\n\n"
        "legacy content body\n",
        encoding="utf-8",
    )
    target_mtime = time.time() - 100 * 86400  # 100 days ago
    os.utime(topic_path, (target_mtime, target_mtime))

    entry = store.get("legacy")
    assert entry is not None
    assert entry.created_at  # backfilled
    backfilled_ts = datetime.fromisoformat(entry.created_at).timestamp()
    assert abs(backfilled_ts - target_mtime) < 2.0


def test_update_preserves_created_at(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    original = store.create(
        slug="beta",
        name="Beta",
        description="d",
        memory_type=MemoryType.PROJECT,
        content="first version",
    )
    time.sleep(0.01)
    updated = store.update("beta", content="second version")
    assert updated.created_at == original.created_at
    assert updated.updated_at != original.updated_at


def test_write_preserves_created_at_on_overwrite(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    first = store.create(
        slug="gamma",
        name="Gamma",
        description="d",
        memory_type=MemoryType.PROJECT,
        content="v1",
    )
    rewritten = store.write(
        TypedMemoryEntry(
            slug="gamma",
            name="Gamma",
            description="d",
            memory_type=MemoryType.PROJECT,
            content="v2 — distilled",
            created_at="",  # simulate distillation forgetting created_at
            updated_at="",
        )
    )
    assert rewritten.created_at == first.created_at


def test_distill_daily_does_not_touch_typed_topics(tmp_path: Path) -> None:
    """distill_daily operates on today-*.md / recent.md / archive.md only,
    never on typed topics/*.md files — so created_at is preserved.
    """
    mem_dir = tmp_path / "mem"
    store = TypedMemoryStore(mem_dir)
    entry = store.create(
        slug="delta",
        name="Delta",
        description="d",
        memory_type=MemoryType.PROJECT,
        content="content",
    )
    original_created = entry.created_at

    # Set up a today-*.md file alongside the typed store and run distillation.
    today = date.today()
    yesterday = today.replace(day=max(1, today.day - 1)) if today.day > 1 else today
    (mem_dir / f"today-{yesterday.isoformat()}.md").write_text(
        "some daily note", encoding="utf-8"
    )
    distill_daily(mem_dir, today)

    after = store.get("delta")
    assert after is not None
    assert after.created_at == original_created
