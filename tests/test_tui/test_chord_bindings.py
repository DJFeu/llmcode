"""Tests for chord parser, default chords, and user override merge."""
from __future__ import annotations

import json
from pathlib import Path

from llm_code.tui.keybindings import (
    KeybindingManager,
    load_keybindings,
    parse_chord,
)


def test_parse_chord_basic():
    assert parse_chord("ctrl+x ctrl+e") == ("ctrl+x", "ctrl+e")


def test_parse_chord_normalizes_case():
    assert parse_chord("Ctrl+X CTRL+E") == ("ctrl+x", "ctrl+e")


def test_default_chords_present():
    mgr = KeybindingManager()
    assert ("ctrl+x", "ctrl+e") in mgr.chord_state.chords
    assert mgr.chord_state.chords[("ctrl+x", "ctrl+e")] == "edit_input_in_external_editor"


def test_chord_state_two_step_match():
    mgr = KeybindingManager()
    assert mgr.chord_state.feed("ctrl+x") is None
    assert mgr.chord_state.feed("ctrl+e") == "edit_input_in_external_editor"


def test_chord_state_reset():
    mgr = KeybindingManager()
    mgr.chord_state.feed("ctrl+x")
    mgr.chord_state.reset()
    assert mgr.chord_state.pending is None


def test_user_chord_override_merge(tmp_path: Path):
    cfg = tmp_path / "kb.json"
    cfg.write_text(json.dumps({
        "chords": {"ctrl+x ctrl+w": "save_session"}
    }))
    mgr = load_keybindings(cfg)
    # User chord present
    assert mgr.chord_state.chords[("ctrl+x", "ctrl+w")] == "save_session"
    # Defaults still present
    assert ("ctrl+x", "ctrl+e") in mgr.chord_state.chords
