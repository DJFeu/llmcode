"""Persistent cross-session cache for SkillRouter Tier C results.

Without this cache, a user restarting the TUI and asking the same
CJK query pays the 14s Tier C LLM classifier round-trip AGAIN
because the in-memory ``self._cache`` is freshly empty. The
persistent cache saves the result to
``~/.llmcode/skill_router_cache.json`` keyed by (skill_set_hash,
query_prefix) so the next session loads it instantly.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from llm_code.runtime import skill_router_cache as src


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(src, "_CACHE_PATH", tmp_path / "skill_router_cache.json")
    yield


def test_load_returns_not_cached_when_no_file() -> None:
    assert src.load_cached_match("今日新聞三則", ["web_search", "read_file"]) is src.NOT_CACHED


def test_save_negative_then_load_returns_none() -> None:
    src.save_match("今日新聞三則", ["web_search", "read_file"], None)
    assert src.load_cached_match("今日新聞三則", ["web_search", "read_file"]) is None


def test_save_positive_then_load_returns_skill_name() -> None:
    src.save_match("explain quicksort", ["coding_patterns", "docs"], "coding_patterns")
    assert src.load_cached_match("explain quicksort", ["coding_patterns", "docs"]) == "coding_patterns"


def test_different_skill_set_invalidates_cache() -> None:
    """Adding or removing a skill changes the skill_set_hash, so
    old cached answers are no longer returned — they might point
    at a deleted skill or miss a newly-added one."""
    src.save_match("query", ["alpha", "beta"], "alpha")
    assert src.load_cached_match("query", ["alpha", "beta"]) == "alpha"
    # Add a new skill — old cache should be invalidated
    assert src.load_cached_match("query", ["alpha", "beta", "gamma"]) is src.NOT_CACHED


def test_skill_set_order_insensitive() -> None:
    """The hash is computed from sorted names, so passing skills
    in a different order must still hit the cache."""
    src.save_match("query", ["beta", "alpha"], "alpha")
    assert src.load_cached_match("query", ["alpha", "beta"]) == "alpha"


def test_different_queries_independent() -> None:
    src.save_match("query1", ["alpha"], "alpha")
    src.save_match("query2", ["alpha"], None)
    assert src.load_cached_match("query1", ["alpha"]) == "alpha"
    assert src.load_cached_match("query2", ["alpha"]) is None
    assert src.load_cached_match("query3", ["alpha"]) is src.NOT_CACHED


def test_query_key_uses_first_200_chars() -> None:
    """Long messages differing only after char 200 should share
    the same cache key, matching the in-memory cache convention."""
    base = "a" * 200
    src.save_match(base + "_SUFFIX_A", ["alpha"], "alpha")
    assert src.load_cached_match(base + "_SUFFIX_B", ["alpha"]) == "alpha"


def test_corrupted_cache_file_returns_not_cached(tmp_path: Path) -> None:
    (tmp_path / "skill_router_cache.json").write_text("not json!", encoding="utf-8")
    assert src.load_cached_match("query", ["alpha"]) is src.NOT_CACHED


def test_clear_cache_wipes_file(tmp_path: Path) -> None:
    src.save_match("q", ["a"], None)
    assert (tmp_path / "skill_router_cache.json").exists()
    src.clear_cache()
    assert not (tmp_path / "skill_router_cache.json").exists()
    assert src.load_cached_match("q", ["a"]) is src.NOT_CACHED


def test_save_preserves_sibling_buckets() -> None:
    """Saving under one skill set must not clobber another
    set's entries."""
    src.save_match("q", ["a", "b"], "a")
    src.save_match("q", ["c", "d"], "c")
    assert src.load_cached_match("q", ["a", "b"]) == "a"
    assert src.load_cached_match("q", ["c", "d"]) == "c"


def test_entries_capped_at_max(tmp_path: Path) -> None:
    """When entries exceed _MAX_ENTRIES, oldest are pruned."""
    # Lower the cap for test speed
    original_cap = src._MAX_ENTRIES
    src._MAX_ENTRIES = 5
    try:
        for i in range(10):
            src.save_match(f"query_{i}", ["alpha"], "alpha")
        # File should have at most 5 entries
        import json
        data = json.loads((tmp_path / "skill_router_cache.json").read_text())
        hash_key = src._compute_skill_set_hash(["alpha"])
        entries = data[hash_key]["entries"]
        assert len(entries) <= 5
    finally:
        src._MAX_ENTRIES = original_cap


def test_atomic_write_no_tmp_files(tmp_path: Path) -> None:
    src.save_match("q", ["a"], None)
    tmp_files = list(tmp_path.glob(".skill_router_cache.*.tmp"))
    assert tmp_files == []
