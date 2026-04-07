"""Tests for the 25KB per-file cap on TypedMemoryStore writes."""
from __future__ import annotations

from pathlib import Path

import pytest

from llm_code.runtime.memory_taxonomy import (
    MemoryFileTooLargeError,
    MemoryType,
    TypedMemoryEntry,
    TypedMemoryStore,
    _MAX_FILE_BYTES,
)


def test_write_raises_when_exceeds_25kb(tmp_path: Path) -> None:
    store = TypedMemoryStore(tmp_path / "mem")
    huge = "x" * (_MAX_FILE_BYTES + 1024)
    entry = TypedMemoryEntry(
        slug="big",
        name="big",
        description="too big",
        memory_type=MemoryType.PROJECT,
        content=huge,
        created_at="",
        updated_at="",
    )
    with pytest.raises(MemoryFileTooLargeError) as exc:
        store.write(entry)
    assert exc.value.slug == "big"
    assert exc.value.size > _MAX_FILE_BYTES
    assert not (store._topics_dir / "big.md").exists()


def test_write_under_cap_succeeds(tmp_path: Path) -> None:
    store = TypedMemoryStore(tmp_path / "mem")
    entry = TypedMemoryEntry(
        slug="small",
        name="small",
        description="ok",
        memory_type=MemoryType.PROJECT,
        content="hello",
        created_at="",
        updated_at="",
    )
    written = store.write(entry)
    assert written.slug == "small"
    assert (store._topics_dir / "small.md").exists()
