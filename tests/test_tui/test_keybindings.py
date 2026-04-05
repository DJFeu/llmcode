"""Tests for keybindings config system."""
from __future__ import annotations

import json

import pytest

from llm_code.tui.keybindings import (
    ACTION_REGISTRY,
    ChordBinding,
    ChordState,
    KeyAction,
    KeybindingManager,
    load_keybindings,
)


class TestKeyAction:
    def test_create(self) -> None:
        action = KeyAction(name="submit", description="Submit input", default_key="enter")
        assert action.name == "submit"
        assert action.default_key == "enter"

    def test_frozen(self) -> None:
        action = KeyAction(name="submit", description="d", default_key="enter")
        with pytest.raises(AttributeError):
            action.name = "x"


class TestActionRegistry:
    def test_has_submit(self) -> None:
        assert "submit" in ACTION_REGISTRY

    def test_has_cancel(self) -> None:
        assert "cancel" in ACTION_REGISTRY

    def test_has_newline(self) -> None:
        assert "newline" in ACTION_REGISTRY

    def test_all_have_default_key(self) -> None:
        for name, action in ACTION_REGISTRY.items():
            assert action.default_key, f"Action '{name}' missing default_key"


class TestChordState:
    def test_no_chord_returns_none(self) -> None:
        state = ChordState(chords={})
        assert state.feed("a") is None

    def test_single_key_not_chord(self) -> None:
        state = ChordState(chords={("ctrl+k", "ctrl+c"): "comment"})
        assert state.feed("a") is None
        assert state.pending is None

    def test_chord_first_key_sets_pending(self) -> None:
        state = ChordState(chords={("ctrl+k", "ctrl+c"): "comment"})
        result = state.feed("ctrl+k")
        assert result is None
        assert state.pending == "ctrl+k"

    def test_chord_second_key_matches(self) -> None:
        state = ChordState(chords={("ctrl+k", "ctrl+c"): "comment"})
        state.feed("ctrl+k")
        result = state.feed("ctrl+c")
        assert result == "comment"
        assert state.pending is None

    def test_chord_second_key_no_match_clears(self) -> None:
        state = ChordState(chords={("ctrl+k", "ctrl+c"): "comment"})
        state.feed("ctrl+k")
        result = state.feed("x")
        assert result is None
        assert state.pending is None

    def test_chord_reset(self) -> None:
        state = ChordState(chords={("ctrl+k", "ctrl+c"): "comment"})
        state.feed("ctrl+k")
        state.reset()
        assert state.pending is None


class TestKeybindingManager:
    def test_default_bindings(self) -> None:
        mgr = KeybindingManager()
        assert mgr.get_action("enter") == "submit"

    def test_rebind(self) -> None:
        mgr = KeybindingManager()
        mgr.rebind("submit", "ctrl+enter")
        assert mgr.get_action("ctrl+enter") == "submit"
        assert mgr.get_action("enter") is None

    def test_conflict_detection(self) -> None:
        mgr = KeybindingManager()
        conflicts = mgr.check_conflict("escape")
        assert len(conflicts) > 0

    def test_reset_single(self) -> None:
        mgr = KeybindingManager()
        mgr.rebind("submit", "ctrl+enter")
        mgr.reset_action("submit")
        assert mgr.get_action("enter") == "submit"

    def test_reset_all(self) -> None:
        mgr = KeybindingManager()
        mgr.rebind("submit", "ctrl+enter")
        mgr.reset_all()
        assert mgr.get_action("enter") == "submit"

    def test_get_all_bindings(self) -> None:
        mgr = KeybindingManager()
        bindings = mgr.get_all_bindings()
        assert "submit" in bindings
        assert bindings["submit"] == "enter"


class TestLoadKeybindings:
    def test_load_from_file(self, tmp_path) -> None:
        config = {"bindings": {"submit": "ctrl+enter"}}
        p = tmp_path / "keybindings.json"
        p.write_text(json.dumps(config))
        mgr = load_keybindings(p)
        assert mgr.get_action("ctrl+enter") == "submit"

    def test_load_missing_file_returns_defaults(self, tmp_path) -> None:
        mgr = load_keybindings(tmp_path / "nonexistent.json")
        assert mgr.get_action("enter") == "submit"

    def test_load_with_chords(self, tmp_path) -> None:
        config = {
            "bindings": {},
            "chords": {"ctrl+k ctrl+c": "comment_selection"},
        }
        p = tmp_path / "keybindings.json"
        p.write_text(json.dumps(config))
        mgr = load_keybindings(p)
        assert mgr.chord_state.feed("ctrl+k") is None
        assert mgr.chord_state.feed("ctrl+c") == "comment_selection"

    def test_conflict_in_file_uses_defaults(self, tmp_path) -> None:
        config = {"bindings": {"submit": "escape", "cancel": "escape"}}
        p = tmp_path / "keybindings.json"
        p.write_text(json.dumps(config))
        mgr = load_keybindings(p)
        assert mgr.get_action("enter") == "submit"
