"""VimEngine — top-level API for the vim editing engine.

Wraps the pure-functional state machine in a mutable shell for
convenient imperative use.
"""
from __future__ import annotations

from llm_code.vim.types import VimMode, VimState, initial_state
from llm_code.vim.transitions import handle_key


class VimEngine:
    """Mutable wrapper around the immutable VimState."""

    def __init__(self, buffer: str = "") -> None:
        self._state = initial_state(buffer)

    @property
    def buffer(self) -> str:
        return self._state.buffer

    @property
    def cursor(self) -> int:
        return self._state.cursor

    @property
    def mode(self) -> VimMode:
        return self._state.mode

    @property
    def mode_display(self) -> str:
        if self._state.mode == VimMode.NORMAL:
            return "-- NORMAL --"
        return "-- INSERT --"

    def feed_key(self, key: str) -> None:
        """Process a single key press."""
        self._state = handle_key(self._state, key)

    def feed_keys(self, keys: str) -> None:
        """Process a sequence of key presses."""
        for k in keys:
            self.feed_key(k)

    def set_buffer(self, buffer: str) -> None:
        """Replace the buffer (used when syncing with external input)."""
        self._state = self._state.with_buffer(buffer, cursor=len(buffer))

    def snapshot(self) -> VimState:
        """Return an immutable snapshot of the current state."""
        return self._state
