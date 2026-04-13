"""Unit tests for VoiceOverlay — tests state transitions without a real recorder."""
from __future__ import annotations

import io

from rich.console import Console

from llm_code.view.repl.coordinator import ScreenCoordinator


def _make_coord() -> ScreenCoordinator:
    capture = io.StringIO()
    console = Console(
        file=capture,
        force_terminal=True,
        color_system="truecolor",
        width=80,
    )
    return ScreenCoordinator(console=console)


# === VoiceOverlay state transitions ===


def test_initial_state_inactive():
    coord = _make_coord()
    assert coord.voice_overlay.is_active is False


def test_start_flips_active():
    coord = _make_coord()
    coord.voice_started()
    assert coord.voice_overlay.is_active is True


def test_start_sets_status_voice_active():
    coord = _make_coord()
    coord.voice_started()
    assert coord.current_status.voice_active is True


def test_start_zeros_progress_fields():
    """Initial voice_seconds and voice_peak are 0, not None."""
    coord = _make_coord()
    coord.voice_started()
    assert coord.current_status.voice_seconds == 0.0
    assert coord.current_status.voice_peak == 0.0


def test_progress_updates_status_fields():
    coord = _make_coord()
    coord.voice_started()
    coord.voice_progress(seconds=2.3, peak=0.42)
    s = coord.current_status
    assert s.voice_seconds == 2.3
    assert s.voice_peak == 0.42


def test_progress_with_zero_peak_still_updates_seconds():
    """Silent frames still advance the timer."""
    coord = _make_coord()
    coord.voice_started()
    coord.voice_progress(seconds=1.5, peak=0.0)
    assert coord.current_status.voice_seconds == 1.5


def test_progress_while_inactive_noop():
    coord = _make_coord()
    coord.voice_progress(seconds=1.0, peak=0.1)
    # Not active — status doesn't flip and progress is ignored.
    assert coord.current_status.voice_active is False


def test_stop_clears_active():
    coord = _make_coord()
    coord.voice_started()
    coord.voice_stopped(reason="manual_stop")
    assert coord.voice_overlay.is_active is False
    assert coord.current_status.voice_active is False


def test_start_is_idempotent():
    coord = _make_coord()
    coord.voice_started()
    coord.voice_started()  # should not raise
    assert coord.voice_overlay.is_active is True


def test_stop_is_idempotent():
    coord = _make_coord()
    coord.voice_started()
    coord.voice_stopped("x")
    coord.voice_stopped("y")  # should not raise
    assert coord.voice_overlay.is_active is False


def test_stop_without_start_is_noop():
    coord = _make_coord()
    coord.voice_stopped("x")  # must not raise


def test_status_line_renders_voice_mode_when_active():
    """When voice is active, the StatusLine render switches to voice mode."""
    coord = _make_coord()
    coord.voice_started()
    coord.voice_progress(seconds=2.3, peak=0.42)
    ft = coord._status_line.render_formatted_text()
    text = "".join(seg[1] for seg in ft)
    assert "🎙" in text
    assert "Ctrl+G" in text


def test_status_line_returns_to_default_after_stop():
    """After voice_stopped, the status line renders the default (no mic emoji)."""
    coord = _make_coord()
    coord.voice_started()
    coord.voice_stopped("manual_stop")
    ft = coord._status_line.render_formatted_text()
    text = "".join(seg[1] for seg in ft)
    assert "🎙" not in text


# === set_voice_toggle_callback ===


def test_set_voice_toggle_callback_stores_handler():
    coord = _make_coord()
    fired: list[bool] = []

    def handler() -> None:
        fired.append(True)

    coord.set_voice_toggle_callback(handler)
    assert coord._voice_toggle_callback is handler


def test_set_voice_toggle_callback_rebuilds_keybindings():
    """After set_voice_toggle_callback, the merged kb contains a c-g binding."""
    from prompt_toolkit.key_binding.key_bindings import _parse_key

    coord = _make_coord()

    def handler() -> None:
        pass

    coord.set_voice_toggle_callback(handler)
    matches = coord._key_bindings.get_bindings_for_keys(
        (_parse_key("c-g"),)
    )
    assert len(matches) >= 1


def test_set_voice_toggle_callback_preserves_dialog_bindings():
    """Rebuilding keybindings must keep the dialog <any>/enter bindings
    merged in — a regression guard for the plan-bug where rebuilding
    discarded the M8 dialog kb."""
    from prompt_toolkit.key_binding.key_bindings import _parse_key

    coord = _make_coord()

    def handler() -> None:
        pass

    coord.set_voice_toggle_callback(handler)
    # Dialog 'enter' binding should still be findable in the merged kb.
    enter_matches = coord._key_bindings.get_bindings_for_keys(
        (_parse_key("enter"),)
    )
    # At least two bindings: input-area 'enter' + dialog 'enter'.
    assert len(enter_matches) >= 2
