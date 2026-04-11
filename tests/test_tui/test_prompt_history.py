"""Tests for the TUI prompt history."""
from __future__ import annotations

from pathlib import Path

import pytest

from llm_code.tui.prompt_history import PromptHistory


# ── in-memory behavior ─────────────────────────────────────────────────


def test_empty_history_prev_returns_none() -> None:
    h = PromptHistory()
    assert h.prev(current="draft") is None
    assert h.next() is None
    assert not h.is_navigating()


def test_add_then_prev_returns_newest_first() -> None:
    h = PromptHistory()
    h.add("first")
    h.add("second")
    h.add("third")

    assert h.prev(current="") == "third"
    assert h.prev(current="") == "second"
    assert h.prev(current="") == "first"
    # Already at oldest — stay put.
    assert h.prev(current="") is None


def test_next_returns_newer_and_then_draft() -> None:
    h = PromptHistory()
    h.add("first")
    h.add("second")

    assert h.prev(current="draft-in-progress") == "second"
    assert h.prev(current="draft-in-progress") == "first"
    assert h.next() == "second"
    # Walking past newest returns the saved draft.
    assert h.next() == "draft-in-progress"
    assert not h.is_navigating()


def test_next_when_not_navigating_returns_none() -> None:
    h = PromptHistory()
    h.add("a")
    assert h.next() is None


def test_consecutive_dedup() -> None:
    h = PromptHistory()
    h.add("same")
    h.add("same")
    h.add("different")
    h.add("same")  # non-consecutive duplicate is kept
    assert h.entries == ["same", "different", "same"]


def test_empty_entries_are_ignored() -> None:
    h = PromptHistory()
    h.add("real")
    h.add("")
    h.add("   \n  ")
    assert h.entries == ["real"]


def test_whitespace_is_stripped() -> None:
    h = PromptHistory()
    h.add("  trimmed  \n")
    assert h.entries == ["trimmed"]


def test_max_entries_bounds_growth() -> None:
    h = PromptHistory(max_entries=3)
    h.add("a")
    h.add("b")
    h.add("c")
    h.add("d")
    assert h.entries == ["d", "c", "b"]
    assert len(h) == 3


def test_add_resets_cursor() -> None:
    h = PromptHistory()
    h.add("old")
    h.add("newer")
    h.prev(current="")
    assert h.is_navigating()
    h.add("brand-new")
    assert not h.is_navigating()


def test_reset_clears_draft() -> None:
    h = PromptHistory()
    h.add("a")
    h.prev(current="draft")
    h.reset()
    assert not h.is_navigating()
    # After reset, next() returns None even though draft used to be set.
    assert h.next() is None


# ── persistence ────────────────────────────────────────────────────────


def test_persist_and_reload(tmp_path: Path) -> None:
    path = tmp_path / "history.txt"
    h1 = PromptHistory(path=path)
    h1.add("alpha")
    h1.add("beta")
    h1.add("gamma")

    # Reload from disk.
    h2 = PromptHistory(path=path)
    assert h2.entries == ["gamma", "beta", "alpha"]
    assert h2.prev(current="") == "gamma"


def test_persist_file_is_oldest_first(tmp_path: Path) -> None:
    """Sanity: the on-disk file is chronological so `tail history.txt`
    shows the newest entries last, matching bash convention."""
    path = tmp_path / "history.txt"
    h = PromptHistory(path=path)
    h.add("one")
    h.add("two")
    h.add("three")

    content = path.read_text(encoding="utf-8")
    lines = content.strip().split("\n")
    assert lines == ["one", "two", "three"]


def test_reload_respects_max_entries(tmp_path: Path) -> None:
    path = tmp_path / "history.txt"
    path.write_text("a\nb\nc\nd\ne\n", encoding="utf-8")
    h = PromptHistory(path=path, max_entries=3)
    # Only the 3 newest (tail) should survive.
    assert h.entries == ["e", "d", "c"]


def test_missing_file_is_silent(tmp_path: Path) -> None:
    path = tmp_path / "does-not-exist.txt"
    h = PromptHistory(path=path)
    assert h.entries == []
    h.add("first")
    assert path.exists()


def test_unreadable_file_does_not_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "history.txt"
    path.write_text("seed\n", encoding="utf-8")

    # Force read_text to raise — PromptHistory should swallow it.
    original_read = Path.read_text

    def boom(self, *a, **kw):
        if self == path:
            raise PermissionError("denied")
        return original_read(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", boom)
    h = PromptHistory(path=path)
    # Failed load means empty entries but the object still works.
    assert h.entries == []
    h.add("recovered")
    assert h.entries == ["recovered"]
