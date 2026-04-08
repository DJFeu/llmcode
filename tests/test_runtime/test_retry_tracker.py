"""Tests for the idempotent retry detector."""
from __future__ import annotations

from llm_code.runtime._retry_tracker import RecentToolCallTracker


def test_first_call_is_never_a_retry_loop() -> None:
    t = RecentToolCallTracker()
    assert t.is_idempotent_retry("web_search", {"query": "x"}) is False


def test_two_consecutive_identical_calls_is_a_retry_loop() -> None:
    t = RecentToolCallTracker()
    t.record("web_search", {"query": "x"})
    assert t.is_idempotent_retry("web_search", {"query": "x"}) is True


def test_two_calls_with_different_args_is_not_a_retry_loop() -> None:
    t = RecentToolCallTracker()
    t.record("web_search", {"query": "x"})
    assert t.is_idempotent_retry("web_search", {"query": "y"}) is False


def test_two_calls_with_different_names_is_not_a_retry_loop() -> None:
    t = RecentToolCallTracker()
    t.record("web_search", {"query": "x"})
    assert t.is_idempotent_retry("read_file", {"file_path": "x"}) is False


def test_after_a_different_call_a_third_identical_call_is_not_a_loop() -> None:
    """A → B → A is fine (the model recovered then went back to A)."""
    t = RecentToolCallTracker()
    t.record("web_search", {"query": "x"})
    t.record("read_file", {"file_path": "y"})
    assert t.is_idempotent_retry("web_search", {"query": "x"}) is False


def test_args_dict_order_does_not_matter() -> None:
    """{"a": 1, "b": 2} and {"b": 2, "a": 1} must hash to the same key."""
    t = RecentToolCallTracker()
    t.record("web_search", {"a": 1, "b": 2})
    assert t.is_idempotent_retry("web_search", {"b": 2, "a": 1}) is True


def test_nested_dict_args_compared_by_value() -> None:
    t = RecentToolCallTracker()
    t.record("web_search", {"args": {"query": "x"}})
    assert t.is_idempotent_retry("web_search", {"args": {"query": "x"}}) is True


def test_list_args_compared_by_value() -> None:
    t = RecentToolCallTracker()
    t.record("bash", {"command": ["ls", "-la"]})
    assert t.is_idempotent_retry("bash", {"command": ["ls", "-la"]}) is True


def test_unhashable_args_does_not_crash() -> None:
    """Defensive: a tool emitting weird args (e.g. raw bytes) must not
    crash the tracker."""
    t = RecentToolCallTracker()
    t.record("weird", {"data": object()})
    # Should return False (cannot determine equality) but not raise.
    assert t.is_idempotent_retry("weird", {"data": object()}) is False
