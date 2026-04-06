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


# ---------------------------------------------------------------------------
# find_related
# ---------------------------------------------------------------------------

class TestFindRelated:
    def test_key_not_found_returns_empty(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        assert store.find_related("nonexistent") == []

    def test_no_relations_returns_empty(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        store.store("lonely", "value")
        assert store.find_related("lonely") == []

    def test_forward_link(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        store.store("A", "concept A", relates_to=("B",))
        store.store("B", "concept B")
        related = store.find_related("A")
        assert len(related) == 1
        assert related[0].key == "B"

    def test_backward_link(self, tmp_path):
        """If B links to A, find_related(A) should include B."""
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        store.store("A", "concept A")
        store.store("B", "concept B", relates_to=("A",))
        related = store.find_related("A")
        assert len(related) == 1
        assert related[0].key == "B"

    def test_bidirectional_links(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        store.store("A", "concept A", relates_to=("B",))
        store.store("B", "concept B", relates_to=("A",))
        related = store.find_related("A")
        assert len(related) == 1
        assert related[0].key == "B"

    def test_multiple_relations(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        store.store("A", "concept A", relates_to=("B", "C"))
        store.store("B", "concept B")
        store.store("C", "concept C")
        related = store.find_related("A")
        assert [e.key for e in related] == ["B", "C"]

    def test_dangling_link_skipped(self, tmp_path):
        """Forward link to non-existent key is silently ignored."""
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        store.store("A", "concept A", relates_to=("ghost",))
        assert store.find_related("A") == []

    def test_results_sorted_by_key(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        store.store("center", "hub", relates_to=("z_node", "a_node"))
        store.store("z_node", "z")
        store.store("a_node", "a")
        related = store.find_related("center")
        assert [e.key for e in related] == ["a_node", "z_node"]


# ---------------------------------------------------------------------------
# find_by_tag
# ---------------------------------------------------------------------------

class TestFindByTag:
    def test_no_matches_returns_empty(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        store.store("key1", "val", tags=("python",))
        assert store.find_by_tag("rust") == []

    def test_empty_store_returns_empty(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        assert store.find_by_tag("anything") == []

    def test_single_match(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        store.store("key1", "val1", tags=("bug_fix",))
        store.store("key2", "val2", tags=("feature",))
        results = store.find_by_tag("bug_fix")
        assert len(results) == 1
        assert results[0].key == "key1"

    def test_multiple_matches(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        store.store("a", "v1", tags=("shared",))
        store.store("b", "v2", tags=("shared", "extra"))
        store.store("c", "v3", tags=("other",))
        results = store.find_by_tag("shared")
        assert len(results) == 2
        keys = {e.key for e in results}
        assert keys == {"a", "b"}

    def test_tag_is_case_sensitive(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        store.store("key1", "v", tags=("Bug",))
        assert store.find_by_tag("bug") == []
        assert len(store.find_by_tag("Bug")) == 1


# ---------------------------------------------------------------------------
# DreamTask._extract_episodes
# ---------------------------------------------------------------------------

class TestExtractEpisodes:
    def _store(self, tmp_path):
        return MemoryStore(tmp_path / "mem", Path("/project/a"))

    def test_extracts_from_json_code_block(self, tmp_path):
        from llm_code.runtime.dream import DreamTask
        store = self._store(tmp_path)
        summary = '''Some text
```json
[{"title": "Fixed auth bug", "type": "bug_fix", "tags": ["auth"], "relates_to": ["login"]}]
```
More text'''
        DreamTask._extract_episodes(summary, store)
        keys = store.list_keys()
        assert len(keys) == 1
        assert keys[0].startswith("episode:")
        assert "Fixed auth bug" in keys[0]

    def test_extracts_from_bare_json(self, tmp_path):
        from llm_code.runtime.dream import DreamTask
        store = self._store(tmp_path)
        summary = 'Here is the result: [{"title": "Refactored DB"}]'
        DreamTask._extract_episodes(summary, store)
        assert len(store.list_keys()) == 1

    def test_no_json_is_noop(self, tmp_path):
        from llm_code.runtime.dream import DreamTask
        store = self._store(tmp_path)
        DreamTask._extract_episodes("Just plain text, no JSON.", store)
        assert store.list_keys() == []

    def test_malformed_json_is_noop(self, tmp_path):
        from llm_code.runtime.dream import DreamTask
        store = self._store(tmp_path)
        DreamTask._extract_episodes('```json\n[{"broken}]\n```', store)
        assert store.list_keys() == []

    def test_type_becomes_tag(self, tmp_path):
        from llm_code.runtime.dream import DreamTask
        store = self._store(tmp_path)
        summary = '[{"title": "Add cache", "type": "feature", "tags": ["perf"]}]'
        DreamTask._extract_episodes(summary, store)
        entry = list(store.get_all().values())[0]
        assert "feature" in entry.tags
        assert "perf" in entry.tags

    def test_relates_to_stored(self, tmp_path):
        from llm_code.runtime.dream import DreamTask
        store = self._store(tmp_path)
        summary = '[{"title": "API redesign", "relates_to": ["auth", "cache"]}]'
        DreamTask._extract_episodes(summary, store)
        entry = list(store.get_all().values())[0]
        assert set(entry.relates_to) == {"auth", "cache"}

    def test_skips_entries_without_title(self, tmp_path):
        from llm_code.runtime.dream import DreamTask
        store = self._store(tmp_path)
        summary = '[{"type": "bug_fix"}, {"title": "Valid entry"}]'
        DreamTask._extract_episodes(summary, store)
        assert len(store.list_keys()) == 1

    def test_multiple_episodes(self, tmp_path):
        from llm_code.runtime.dream import DreamTask
        store = self._store(tmp_path)
        summary = '[{"title": "Episode 1"}, {"title": "Episode 2"}, {"title": "Episode 3"}]'
        DreamTask._extract_episodes(summary, store)
        assert len(store.list_keys()) == 3
