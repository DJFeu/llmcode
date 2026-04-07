"""Tests for SpinnerLine animation: smooth tokens, width gating, stall color."""
from __future__ import annotations

from unittest.mock import patch

from llm_code.tui.chat_widgets import SpinnerLine


def _spinner() -> SpinnerLine:
    return SpinnerLine()


def test_displayed_tokens_advances_toward_target() -> None:
    s = _spinner()
    s.tokens = 800
    assert s._displayed_tokens == 0
    prev = 0.0
    for _ in range(20):
        s._advance_tokens()
        assert s._displayed_tokens >= prev
        prev = s._displayed_tokens
    # Should be getting close to target over multiple ticks
    assert s._displayed_tokens > 200


def test_displayed_tokens_stops_at_target() -> None:
    s = _spinner()
    s.tokens = 5
    for _ in range(50):
        s._advance_tokens()
    assert s._displayed_tokens == 5


def test_width_below_40_strips_suffix() -> None:
    s = _spinner()
    s.phase = "thinking"
    s._verb = "Puttering"
    s.elapsed = 10
    s._displayed_tokens = 1000
    with patch.object(SpinnerLine, "_terminal_width", return_value=30):
        out = s.render_text()
    # Should be just the verb…, no elapsed/tokens parens
    assert out == "Puttering…"


def test_width_below_60_drops_tokens() -> None:
    s = _spinner()
    s.phase = "thinking"
    s._verb = "Puttering"
    s.elapsed = 10
    s._displayed_tokens = 1000
    with patch.object(SpinnerLine, "_terminal_width", return_value=50):
        out = s.render_text()
    assert "tokens" not in out
    assert "10s" in out


def test_elapsed_below_3s_hides_time() -> None:
    s = _spinner()
    s.phase = "thinking"
    s._verb = "Puttering"
    s.elapsed = 1.0
    with patch.object(SpinnerLine, "_terminal_width", return_value=120):
        out = s.render_text()
    assert "s" not in out.replace("Puttering…", "")  # no elapsed segment


def test_stalled_color_leans_red_after_61s() -> None:
    s = _spinner()
    s.phase = "thinking"
    s.elapsed = 0
    s._last_progress = 0
    # Simulate 61s of no progress
    s.elapsed = 61
    r, g, b = s._stall_rgb()
    base_r, base_g, base_b = SpinnerLine._BASE_RGB
    stalled_r, _, _ = SpinnerLine._STALLED_RGB
    # Red channel should move toward stalled red (171) from base (96)
    assert r > base_r
    # Should be at or near the fully-stalled red after 60s of stall
    assert abs(r - stalled_r) < 10


def test_fresh_state_uses_base_rgb() -> None:
    s = _spinner()
    s.elapsed = 5
    s._last_progress = 5
    assert s._stall_rgb() == SpinnerLine._BASE_RGB


def test_tokens_increase_resets_progress_anchor() -> None:
    s = _spinner()
    s.elapsed = 50
    s._last_progress = 0
    s.watch_tokens(0, 100)
    assert s._last_progress == 50


def test_phase_change_picks_new_verb() -> None:
    s = _spinner()
    assert s._verb == ""
    s.watch_phase("waiting", "thinking")
    assert s._verb  # non-empty verb picked


def test_advance_frame_ticks_tokens() -> None:
    s = _spinner()
    s.tokens = 80
    s.advance_frame()
    assert s._displayed_tokens > 0
