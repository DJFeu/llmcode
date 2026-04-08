"""Verify the runtime aborts when the model emits the same tool call
twice in a row with the same args."""
from __future__ import annotations

from llm_code.runtime._retry_tracker import RecentToolCallTracker


def test_repeated_call_signature_is_caught() -> None:
    """Direct unit-level proof that the tracker catches the failure
    pattern from the 2026-04-08 production loop (76K tokens, 3.6 min)."""
    t = RecentToolCallTracker()
    # Model emits web_search with empty args (parser bug)
    t.record("web_search", {})
    # Model retries with same empty args after error
    assert t.is_idempotent_retry("web_search", {}) is True


def test_recovery_after_one_loop_detection() -> None:
    """If the model recovers (different args), tracking continues."""
    t = RecentToolCallTracker()
    t.record("web_search", {})
    assert t.is_idempotent_retry("web_search", {}) is True
    # After we abort, the model gets the error and emits a different call.
    # The tracker (newly instantiated for the next turn) starts fresh.
    t2 = RecentToolCallTracker()
    assert t2.is_idempotent_retry("web_search", {"query": "今日新聞"}) is False
