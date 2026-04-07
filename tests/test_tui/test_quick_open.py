"""Tests for Quick Open fuzzy file finder."""
from __future__ import annotations

from pathlib import Path

from llm_code.tui.quick_open import fuzzy_find_files


CANDIDATES = [
    "llm_code/tui/app.py",
    "llm_code/tui/status_bar.py",
    "llm_code/runtime/conversation.py",
    "tests/test_tui/test_app.py",
    "README.md",
]


def test_empty_query_returns_first_n():
    results = fuzzy_find_files("", Path("."), limit=3, candidates=CANDIDATES)
    assert len(results) == 3


def test_substring_match_ranked_first():
    results = fuzzy_find_files("status_bar", Path("."), candidates=CANDIDATES)
    assert results[0].path == "llm_code/tui/status_bar.py"


def test_fuzzy_fallback():
    results = fuzzy_find_files("convrs", Path("."), candidates=CANDIDATES)
    paths = [r.path for r in results]
    assert "llm_code/runtime/conversation.py" in paths


def test_limit_respected():
    results = fuzzy_find_files("py", Path("."), limit=2, candidates=CANDIDATES)
    assert len(results) <= 2


def test_no_match_returns_empty():
    results = fuzzy_find_files("zzzzzxxxxxqqqqq", Path("."), candidates=CANDIDATES)
    assert results == []
