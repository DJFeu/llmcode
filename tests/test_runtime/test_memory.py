"""Tests for llm_code.runtime.memory — TDD (RED first)."""
from __future__ import annotations

from pathlib import Path

import pytest

from llm_code.runtime.memory import MemoryEntry, MemoryStore


class TestMemoryEntry:
    def test_is_frozen(self):
        entry = MemoryEntry(
            key="k", value="v", created_at="2024-01-01T00:00:00+00:00", updated_at="2024-01-01T00:00:00+00:00"
        )
        with pytest.raises((AttributeError, TypeError)):
            entry.key = "other"  # type: ignore[misc]


class TestMemoryStore:
    def test_store_and_recall(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        store.store("lang", "Python")
        assert store.recall("lang") == "Python"

    def test_store_overwrites_existing(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        store.store("lang", "Python")
        store.store("lang", "Go")
        assert store.recall("lang") == "Go"

    def test_recall_missing_returns_none(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        assert store.recall("nonexistent") is None

    def test_list_keys(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        store.store("a", "1")
        store.store("b", "2")
        keys = store.list_keys()
        assert sorted(keys) == ["a", "b"]

    def test_delete(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        store.store("lang", "Python")
        store.delete("lang")
        assert store.recall("lang") is None
        assert "lang" not in store.list_keys()

    def test_delete_nonexistent_is_noop(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        store.delete("ghost")  # should not raise

    def test_get_all_returns_memory_entries(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        store.store("x", "hello")
        store.store("y", "world")
        all_entries = store.get_all()
        assert isinstance(all_entries, dict)
        assert "x" in all_entries and "y" in all_entries
        assert isinstance(all_entries["x"], MemoryEntry)
        assert all_entries["x"].value == "hello"
        assert all_entries["x"].key == "x"

    def test_get_all_has_timestamps(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        store.store("ts", "value")
        entry = store.get_all()["ts"]
        assert entry.created_at
        assert entry.updated_at

    def test_overwrite_updates_updated_at_not_created_at(self, tmp_path):
        import time
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        store.store("k", "v1")
        time.sleep(0.01)
        store.store("k", "v2")
        entry = store.get_all()["k"]
        assert entry.created_at <= entry.updated_at

    def test_save_session_summary_creates_file(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        store.save_session_summary("Session was about Python.")
        files = list((tmp_path / "mem").rglob("*.md"))
        assert len(files) == 1
        assert "Session was about Python." in files[0].read_text()

    def test_load_recent_summaries_returns_in_reverse_order(self, tmp_path):
        import time
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        store.save_session_summary("first")
        time.sleep(1.1)  # filenames are minute-precision; ensure different names
        store.save_session_summary("second")
        summaries = store.load_recent_summaries(limit=5)
        # most recent first
        assert summaries[0] == "second"
        assert summaries[1] == "first"

    def test_load_recent_summaries_respects_limit(self, tmp_path):
        import time
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        for i in range(3):
            store.save_session_summary(f"summary {i}")
            time.sleep(1.1)
        summaries = store.load_recent_summaries(limit=2)
        assert len(summaries) == 2

    def test_load_recent_summaries_empty_returns_empty_list(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        assert store.load_recent_summaries() == []

    def test_project_hash_isolation(self, tmp_path):
        mem_root = tmp_path / "mem"
        store_a = MemoryStore(mem_root, Path("/project/alpha"))
        store_b = MemoryStore(mem_root, Path("/project/beta"))
        store_a.store("key", "from-alpha")
        # store_b should not see store_a's data
        assert store_b.recall("key") is None

    def test_different_project_paths_create_different_dirs(self, tmp_path):
        mem_root = tmp_path / "mem"
        store_a = MemoryStore(mem_root, Path("/project/alpha"))
        store_b = MemoryStore(mem_root, Path("/project/beta"))
        assert store_a._dir != store_b._dir

    def test_same_project_path_creates_same_dir(self, tmp_path):
        mem_root = tmp_path / "mem"
        store_a = MemoryStore(mem_root, Path("/project/same"))
        store_b = MemoryStore(mem_root, Path("/project/same"))
        assert store_a._dir == store_b._dir

    def test_corrupted_memory_file_returns_empty(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        store._memory_file.write_text("not valid json")
        assert store._load() == {}
        assert store.list_keys() == []

    def test_get_all_empty_returns_empty_dict(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        assert store.get_all() == {}

    def test_consolidated_dir_property(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        cdir = store.consolidated_dir
        assert cdir.name == "consolidated"
        assert cdir.parent == store._dir
        assert cdir.is_dir()

    def test_save_consolidated_creates_file(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        store.save_consolidated("# 2026-04-03 Summary\nModified: foo.py")
        files = list(store.consolidated_dir.glob("*.md"))
        assert len(files) == 1
        assert "Modified: foo.py" in files[0].read_text()

    def test_save_consolidated_uses_date_filename(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        store.save_consolidated("summary content", date_str="2026-04-03")
        path = store.consolidated_dir / "2026-04-03.md"
        assert path.exists()
        assert path.read_text(encoding="utf-8") == "summary content"

    def test_load_consolidated_summaries_reverse_order(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        store.save_consolidated("first", date_str="2026-04-01")
        store.save_consolidated("second", date_str="2026-04-02")
        summaries = store.load_consolidated_summaries(limit=5)
        assert summaries[0] == "second"
        assert summaries[1] == "first"

    def test_load_consolidated_summaries_respects_limit(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        for i in range(5):
            store.save_consolidated(f"summary {i}", date_str=f"2026-04-0{i+1}")
        summaries = store.load_consolidated_summaries(limit=2)
        assert len(summaries) == 2

    def test_load_consolidated_summaries_empty(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        assert store.load_consolidated_summaries() == []
