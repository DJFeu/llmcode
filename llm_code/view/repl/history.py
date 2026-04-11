"""Prompt history for the TUI InputBar.

Shell-style recall: submitted prompts are appended to an in-memory list
and persisted to ``~/.llmcode/prompt_history.txt``. The InputBar calls
:meth:`PromptHistory.prev` when the user presses ↑ on a single-line
buffer and :meth:`PromptHistory.next` on ↓. The first ``prev`` snapshots
the current composing buffer as *draft* so the user can always get back
to it by pressing ↓ past the newest entry.

Design notes
------------
* The history is **append-only with consecutive dedup** — pressing Enter
  twice on the same prompt only stores it once, matching bash / zsh
  ``HISTCONTROL=ignoredups``.
* The cache is bounded (``max_entries``, default 1000). When the bound
  is hit, the oldest entry is dropped.
* Persistence is best-effort: a permission or I/O error silently leaves
  the on-disk file alone and the in-memory state continues to work.
* The class is pure-Python, no Textual imports, so it can be unit-tested
  without spinning up a widget tree.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_MAX_ENTRIES = 1000


class PromptHistory:
    """In-memory prompt history with optional file persistence.

    Cursor semantics mirror shell history:

    * ``_cursor == -1`` — the user is composing a new entry (not
      navigating history). ``prev`` and ``next`` are both no-ops in
      opposite directions.
    * ``_cursor == 0`` — pointing at the most recently submitted entry.
    * ``_cursor == len(entries) - 1`` — pointing at the oldest entry.

    ``prev`` walks toward older entries (increasing the cursor). ``next``
    walks toward newer entries and, on stepping past index 0, restores
    the saved draft and returns it (cursor goes back to -1).
    """

    def __init__(
        self,
        path: Optional[Path] = None,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        self._path = path
        self._max_entries = max_entries
        self._entries: list[str] = []
        self._cursor: int = -1
        self._draft: str = ""
        if path is not None:
            self._load()

    # ── persistence ────────────────────────────────────────────────────

    def _load(self) -> None:
        """Read history from disk, silently ignoring read errors."""
        if self._path is None or not self._path.exists():
            return
        try:
            raw = self._path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.debug("prompt history load failed: %s", exc)
            return
        # File is oldest-first (bash-like); keep as oldest-first in-memory
        # behind the cursor view so prev() walks back through time.
        lines = [ln.rstrip("\n") for ln in raw.split("\n") if ln.strip()]
        # Reverse so the newest entry is at index 0 (matches cursor semantics).
        self._entries = list(reversed(lines[-self._max_entries:]))

    def _persist(self) -> None:
        """Write history to disk; oldest-first so `tail` shows newest."""
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # Write oldest → newest so the file is human-readable top-to-bottom.
            oldest_first = list(reversed(self._entries))
            self._path.write_text(
                "\n".join(oldest_first) + ("\n" if oldest_first else ""),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.debug("prompt history persist failed: %s", exc)

    # ── public API ─────────────────────────────────────────────────────

    @property
    def entries(self) -> list[str]:
        """Read-only snapshot of the stored entries, newest first."""
        return list(self._entries)

    def peek_latest(self) -> Optional[str]:
        """Return the most recent entry without advancing the cursor.

        Used by the M15 history ghost text processor: when the buffer
        is empty, the ghost previews the latest entry; pressing Tab
        or Right accepts it.
        """
        if not self._entries:
            return None
        return self._entries[0]

    def count_entries(self) -> int:
        """Return the number of stored entries (O(1))."""
        return len(self._entries)

    def search(self, query: str, *, limit: int = 20) -> list[str]:
        """Return entries whose text contains ``query`` (newest first).

        Case-insensitive substring match. Used by Ctrl+R history search
        in a future M15 follow-up.
        """
        if not query:
            return []
        needle = query.lower()
        out: list[str] = []
        for e in self._entries:
            if needle in e.lower():
                out.append(e)
                if len(out) >= limit:
                    break
        return out

    def __len__(self) -> int:
        return len(self._entries)

    def add(self, entry: str) -> None:
        """Record a submitted prompt.

        Consecutive duplicates are skipped (``add('x'); add('x')`` stores
        one entry). Empty or whitespace-only entries are ignored. The
        cursor is reset to ``-1`` so the next ``prev`` starts fresh from
        the newest item.
        """
        stripped = entry.strip()
        if not stripped:
            self.reset()
            return
        if self._entries and self._entries[0] == stripped:
            self.reset()
            return
        self._entries.insert(0, stripped)
        if len(self._entries) > self._max_entries:
            self._entries = self._entries[: self._max_entries]
        self.reset()
        self._persist()

    def reset(self) -> None:
        """Stop history navigation; forget any saved draft."""
        self._cursor = -1
        self._draft = ""

    def prev(self, current: str) -> Optional[str]:
        """Walk one step toward older entries.

        Returns the entry at the new cursor, or ``None`` if the history
        is empty / the cursor is already at the oldest entry.

        The first call snapshots ``current`` as the draft so ``next``
        can restore it when walking back past the newest entry.
        """
        if not self._entries:
            return None
        if self._cursor == -1:
            self._draft = current
            self._cursor = 0
            return self._entries[0]
        if self._cursor + 1 >= len(self._entries):
            return None  # already at oldest — stay put, match shell behavior
        self._cursor += 1
        return self._entries[self._cursor]

    def next(self) -> Optional[str]:
        """Walk one step toward newer entries.

        Returns the entry at the new cursor, or the saved draft when the
        cursor walks past index 0. Returns ``None`` when not currently
        navigating history (cursor == -1).
        """
        if self._cursor == -1:
            return None
        if self._cursor == 0:
            draft = self._draft
            self.reset()
            return draft
        self._cursor -= 1
        return self._entries[self._cursor]

    def is_navigating(self) -> bool:
        """``True`` when the user is currently walking the history."""
        return self._cursor != -1


def default_history_path() -> Path:
    """Return the canonical history file path (``~/.llmcode/prompt_history.txt``)."""
    return Path(os.path.expanduser("~/.llmcode/prompt_history.txt"))
