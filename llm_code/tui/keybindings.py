"""Keybinding configuration — action registry, chord support, config loader."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class KeyAction:
    """A bindable action with a default key."""
    name: str
    description: str
    default_key: str


ACTION_REGISTRY: dict[str, KeyAction] = {
    "submit": KeyAction("submit", "Submit input", "enter"),
    "newline": KeyAction("newline", "Insert newline", "shift+enter"),
    "cancel": KeyAction("cancel", "Cancel / clear input", "escape"),
    "clear_input": KeyAction("clear_input", "Clear input line", "ctrl+u"),
    "autocomplete": KeyAction("autocomplete", "Autocomplete slash command", "tab"),
    "history_prev": KeyAction("history_prev", "Previous history", "ctrl+p"),
    "history_next": KeyAction("history_next", "Next history", "ctrl+n"),
    "toggle_thinking": KeyAction("toggle_thinking", "Toggle thinking display", "alt+t"),
    "toggle_vim": KeyAction("toggle_vim", "Toggle vim mode", "ctrl+shift+v"),
    "voice_input": KeyAction("voice_input", "Activate voice input", "ctrl+space"),
    "cursor_left": KeyAction("cursor_left", "Move cursor left", "left"),
    "cursor_right": KeyAction("cursor_right", "Move cursor right", "right"),
    "cursor_home": KeyAction("cursor_home", "Move to line start", "home"),
    "cursor_end": KeyAction("cursor_end", "Move to line end", "end"),
    "delete_back": KeyAction("delete_back", "Delete char before cursor", "backspace"),
    "delete_forward": KeyAction("delete_forward", "Delete char at cursor", "delete"),
}


@dataclass(frozen=True)
class ChordBinding:
    """A two-key chord mapping."""
    keys: tuple[str, ...]
    action: str


@dataclass
class ChordState:
    """Tracks chord key sequences."""
    chords: dict[tuple[str, ...], str] = field(default_factory=dict)
    pending: str | None = None

    def feed(self, key: str) -> str | None:
        if self.pending is not None:
            combo = (self.pending, key)
            self.pending = None
            return self.chords.get(combo)
        for chord_keys in self.chords:
            if chord_keys[0] == key:
                self.pending = key
                return None
        return None

    def reset(self) -> None:
        self.pending = None


class KeybindingManager:
    def __init__(self) -> None:
        self._bindings: dict[str, str] = {}
        self._reverse: dict[str, str] = {}
        self.chord_state = ChordState()
        self.reset_all()

    def get_action(self, key: str) -> str | None:
        return self._bindings.get(key)

    def get_key(self, action: str) -> str | None:
        return self._reverse.get(action)

    def rebind(self, action: str, new_key: str) -> None:
        old_key = self._reverse.get(action)
        if old_key and old_key in self._bindings:
            del self._bindings[old_key]
        self._bindings[new_key] = action
        self._reverse[action] = new_key

    def check_conflict(self, key: str) -> list[str]:
        action = self._bindings.get(key)
        return [action] if action else []

    def reset_action(self, action: str) -> None:
        if action not in ACTION_REGISTRY:
            return
        old_key = self._reverse.get(action)
        if old_key and old_key in self._bindings:
            del self._bindings[old_key]
        default_key = ACTION_REGISTRY[action].default_key
        self._bindings[default_key] = action
        self._reverse[action] = default_key

    def reset_all(self) -> None:
        self._bindings.clear()
        self._reverse.clear()
        for name, action in ACTION_REGISTRY.items():
            self._bindings[action.default_key] = name
            self._reverse[name] = action.default_key

    def get_all_bindings(self) -> dict[str, str]:
        return dict(self._reverse)


def load_keybindings(path: Path) -> KeybindingManager:
    mgr = KeybindingManager()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return mgr

    bindings = data.get("bindings", {})
    if isinstance(bindings, dict):
        key_to_actions: dict[str, list[str]] = {}
        for action, key in bindings.items():
            key_to_actions.setdefault(key, []).append(action)
        has_conflict = any(len(actions) > 1 for actions in key_to_actions.values())
        if has_conflict:
            _log.warning("Keybinding config has conflicts; using defaults")
            return KeybindingManager()
        for action, key in bindings.items():
            if action in ACTION_REGISTRY:
                mgr.rebind(action, key)

    chords_raw = data.get("chords", {})
    if isinstance(chords_raw, dict):
        chords: dict[tuple[str, ...], str] = {}
        for key_str, action in chords_raw.items():
            keys = tuple(key_str.split())
            if len(keys) == 2:
                chords[keys] = action
        mgr.chord_state = ChordState(chords=chords)

    return mgr
