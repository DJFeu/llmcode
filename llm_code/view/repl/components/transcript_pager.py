"""Transcript pager — full-history scrollback over the SQLite state DB.

Borrowed from Codex's ``pager_overlay.rs``: the active REPL only
shows the most recent turns inline; pressing ``?`` (or running the
``/transcript`` slash command) opens this pager so the user can
scroll back through the full conversation. The data source is
:class:`llm_code.runtime.state_db.StateDB` so the pager works for any
session that has been written to disk.

The pager is intentionally model-first: :class:`TranscriptPager` is a
plain Python class with no prompt_toolkit dependency. Tests exercise
it directly. The view backend (REPL) wires keybindings and renders
``current_view()`` into a ``Float`` over the standard layout — that
glue lives in the coordinator and is left as a thin shim because the
data + interaction model is what's worth covering with tests.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Iterable

from llm_code.runtime.state_db import StateDB, TurnRecord

logger = logging.getLogger(__name__)


_MAX_TURNS_DEFAULT = 50


@dataclasses.dataclass(frozen=True)
class PagerLine:
    """One rendered line in the pager's scrollback buffer."""

    text: str
    is_match: bool = False


@dataclasses.dataclass(frozen=True)
class SearchState:
    needle: str
    matches: tuple[int, ...]  # 0-indexed line numbers
    cursor: int  # index into matches; -1 when no matches


def _format_turn(turn: TurnRecord) -> list[str]:
    """Render a single turn into a list of pager lines (oldest-first)."""
    lines: list[str] = [f"── turn {turn.idx} ──"]
    if turn.user_message:
        lines.append(f"user: {turn.user_message}")
    if turn.assistant_message:
        lines.append(f"assistant: {turn.assistant_message}")
    lines.append("")  # blank separator
    return lines


def render_lines(turns: Iterable[TurnRecord]) -> list[str]:
    """Flatten a sequence of turns into a flat list of pager lines."""
    out: list[str] = []
    for turn in turns:
        out.extend(_format_turn(turn))
    if out and out[-1] == "":
        out.pop()
    return out


# ── pager model ───────────────────────────────────────────────────────


class TranscriptPager:
    """Scrollable, searchable view over a session's transcript.

    Lifecycle (matches Codex's ``?`` overlay behaviour):

    1. Caller constructs the pager with a session id; lines load
       lazily on :meth:`open`.
    2. Cursor starts at the end (``last`` line). Up/Down adjust by 1;
       Page Up/Down adjust by the viewport height; ``g``/``G``
       jump to start/end.
    3. ``/`` enters search mode (``begin_search``); typing builds the
       needle; Enter materialises matches (``commit_search``); ``n``/
       ``N`` jump to the next/previous match.
    4. ``q``/Esc closes via :meth:`close` — the caller drops the
       pager and returns focus to the prompt.
    """

    def __init__(
        self,
        state_db: StateDB,
        session_id: str,
        max_turns: int = _MAX_TURNS_DEFAULT,
        viewport_height: int = 20,
    ) -> None:
        self._db = state_db
        self._session_id = session_id
        self._max_turns = max_turns
        self._viewport_height = max(1, int(viewport_height))
        self._lines: list[str] = []
        self._cursor: int = 0
        self._open: bool = False
        self._search: SearchState | None = None
        self._search_buffer: str = ""

    # ── lifecycle ────────────────────────────────────────────────────

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def is_open(self) -> bool:
        return self._open

    @property
    def cursor(self) -> int:
        return self._cursor

    @property
    def viewport_height(self) -> int:
        return self._viewport_height

    @property
    def line_count(self) -> int:
        return len(self._lines)

    @property
    def search(self) -> SearchState | None:
        return self._search

    def open(self) -> None:
        """Load turns from the state DB and position cursor at the end."""
        turns = self._db.load_recent_turns(self._session_id, count=self._max_turns)
        self._lines = render_lines(turns)
        # Cursor at the last viewport so the most recent turns are visible.
        self._cursor = max(0, len(self._lines) - self._viewport_height)
        self._open = True

    def close(self) -> None:
        self._open = False
        self._lines = []
        self._cursor = 0
        self._search = None
        self._search_buffer = ""

    # ── navigation ───────────────────────────────────────────────────

    def scroll_up(self, by: int = 1) -> None:
        self._cursor = max(0, self._cursor - max(1, by))

    def scroll_down(self, by: int = 1) -> None:
        max_top = max(0, len(self._lines) - self._viewport_height)
        self._cursor = min(max_top, self._cursor + max(1, by))

    def page_up(self) -> None:
        self.scroll_up(self._viewport_height)

    def page_down(self) -> None:
        self.scroll_down(self._viewport_height)

    def goto_start(self) -> None:
        self._cursor = 0

    def goto_end(self) -> None:
        self._cursor = max(0, len(self._lines) - self._viewport_height)

    # ── search ───────────────────────────────────────────────────────

    def begin_search(self) -> None:
        self._search_buffer = ""
        self._search = SearchState(needle="", matches=(), cursor=-1)

    def update_search_buffer(self, ch: str) -> None:
        if not self._search:
            return
        self._search_buffer += ch

    def backspace_search(self) -> None:
        if not self._search:
            return
        self._search_buffer = self._search_buffer[:-1]

    def cancel_search(self) -> None:
        self._search = None
        self._search_buffer = ""

    def commit_search(self) -> SearchState | None:
        """Materialise matches for the current ``_search_buffer``.

        Empty needle clears the search state. Returns the new state
        for the caller's UI feedback; ``None`` means search was
        cleared. ``SearchState.cursor`` is an *index into matches*
        (0-based), not a line number — call sites use
        ``matches[cursor]`` to get the actual line number.
        """
        needle = self._search_buffer
        if not needle:
            self._search = None
            return None
        matches = tuple(
            i for i, line in enumerate(self._lines)
            if needle.lower() in line.lower()
        )
        cursor = 0 if matches else -1
        self._search = SearchState(needle=needle, matches=matches, cursor=cursor)
        if cursor >= 0:
            self._snap_cursor_to_match()
        return self._search

    def next_match(self) -> SearchState | None:
        if not self._search or not self._search.matches:
            return self._search
        next_idx = (self._search.cursor + 1) % len(self._search.matches)
        self._search = dataclasses.replace(self._search, cursor=next_idx)
        self._snap_cursor_to_match()
        return self._search

    def prev_match(self) -> SearchState | None:
        if not self._search or not self._search.matches:
            return self._search
        prev_idx = (self._search.cursor - 1) % len(self._search.matches)
        self._search = dataclasses.replace(self._search, cursor=prev_idx)
        self._snap_cursor_to_match()
        return self._search

    def _snap_cursor_to_match(self) -> None:
        if not self._search or self._search.cursor < 0:
            return
        line = self._search.matches[self._search.cursor]
        # Centre the match in the viewport when possible.
        target = max(0, line - self._viewport_height // 2)
        max_top = max(0, len(self._lines) - self._viewport_height)
        self._cursor = min(max_top, target)

    # ── rendering ────────────────────────────────────────────────────

    def current_view(self) -> list[PagerLine]:
        """Return the visible slice of lines + match flags."""
        slice_start = self._cursor
        slice_end = slice_start + self._viewport_height
        out: list[PagerLine] = []
        match_lines = (
            set(self._search.matches) if self._search and self._search.matches
            else set()
        )
        for i in range(slice_start, slice_end):
            if i >= len(self._lines):
                break
            out.append(PagerLine(text=self._lines[i], is_match=i in match_lines))
        return out

    def status_line(self) -> str:
        """One-line summary for the pager footer.

        Reports the cursor position, total lines, and (if active) the
        current search progress. The slash-command surface uses this
        to render a status bar without poking the internals.
        """
        total = len(self._lines)
        bottom = min(total, self._cursor + self._viewport_height)
        parts = [f"lines {self._cursor + 1}-{bottom}/{total}"]
        if self._search and self._search.matches:
            cur = self._search.cursor + 1
            parts.append(f"match {cur}/{len(self._search.matches)} for '{self._search.needle}'")
        elif self._search and self._search_buffer:
            parts.append(f"searching '{self._search_buffer}' (no matches)")
        return " · ".join(parts)
