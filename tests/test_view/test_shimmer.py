"""Tests for the shimmer text helper (M15 Task A4)."""
from __future__ import annotations

from llm_code.view.repl.components.shimmer import reset_cache, shimmer_text


def setup_function(_fn) -> None:
    reset_cache()


def test_empty_string_returns_empty_list() -> None:
    assert shimmer_text("") == []


def test_one_char_per_tuple() -> None:
    out = shimmer_text("hello", now=0.0)
    assert len(out) == 5
    for style_str, ch in out:
        assert len(ch) == 1
        assert style_str.startswith("fg:")


def test_neighboring_chars_have_distinct_styles() -> None:
    out = shimmer_text("ABCDE", now=0.0, per_char_offset=0.2)
    styles = [s for s, _ in out]
    # At least two distinct gradient stops across the 5 chars.
    assert len(set(styles)) >= 2


def test_time_progression_changes_colors() -> None:
    reset_cache()
    a = shimmer_text("x", now=0.0)
    reset_cache()
    b = shimmer_text("x", now=0.6)
    assert a != b


def test_cache_returns_stable_result_within_window() -> None:
    first = shimmer_text("hello", now=10.0)
    second = shimmer_text("hello", now=10.05)
    # Same 100ms bucket → identical cached object.
    assert first == second
