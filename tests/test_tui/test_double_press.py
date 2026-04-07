"""Tests for double-press confirmation tracker."""
from __future__ import annotations

from llm_code.tui.double_press import DoublePressTracker


def test_first_press_does_not_confirm():
    t = DoublePressTracker()
    assert t.press("ctrl+c", 0.0) is False


def test_second_press_within_window_confirms():
    t = DoublePressTracker(window=1.5)
    t.press("ctrl+c", 0.0)
    assert t.press("ctrl+c", 1.0) is True


def test_second_press_outside_window_does_not_confirm():
    t = DoublePressTracker(window=1.5)
    t.press("ctrl+c", 0.0)
    assert t.press("ctrl+c", 2.0) is False


def test_independent_keys_tracked_separately():
    t = DoublePressTracker()
    t.press("ctrl+c", 0.0)
    assert t.press("ctrl+d", 0.5) is False  # different key, no confirm
    assert t.press("ctrl+c", 1.0) is True


def test_reset_clears_pending():
    t = DoublePressTracker()
    t.press("ctrl+c", 0.0)
    t.reset()
    assert t.press("ctrl+c", 0.5) is False
