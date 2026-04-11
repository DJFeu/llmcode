"""Tests for the history ghost processor (M15 Task B3)."""
from __future__ import annotations

from llm_code.view.repl.components.history_ghost import HistoryGhostProcessor
from llm_code.view.repl.history import PromptHistory


def test_peek_latest_returns_most_recent() -> None:
    h = PromptHistory()
    h.add("first")
    h.add("second")
    assert h.peek_latest() == "second"


def test_peek_latest_on_empty_history() -> None:
    h = PromptHistory()
    assert h.peek_latest() is None


def test_count_entries() -> None:
    h = PromptHistory()
    assert h.count_entries() == 0
    h.add("one")
    h.add("two")
    assert h.count_entries() == 2


def test_search_substring_match() -> None:
    h = PromptHistory()
    h.add("git status")
    h.add("git diff")
    h.add("ls -la")
    results = h.search("git")
    assert "git status" in results
    assert "git diff" in results
    assert "ls -la" not in results


def test_search_is_case_insensitive() -> None:
    h = PromptHistory()
    h.add("Git Status")
    assert "Git Status" in h.search("git")


def test_search_respects_limit() -> None:
    h = PromptHistory()
    for i in range(30):
        h.add(f"entry {i}")
    assert len(h.search("entry", limit=5)) == 5


def test_search_empty_query_returns_nothing() -> None:
    h = PromptHistory()
    h.add("something")
    assert h.search("") == []


def test_history_ghost_processor_constructs() -> None:
    h = PromptHistory()
    h.add("test entry")
    proc = HistoryGhostProcessor(peek=h.peek_latest)
    assert proc is not None
