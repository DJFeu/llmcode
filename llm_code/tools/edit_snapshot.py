"""Tool-level edit snapshot store (C5).

Opencode's ``snapshot/index.ts`` inspires this: every successful
edit_file / multi_edit call records a :class:`EditSnapshot` so the
runtime can offer ``/undo`` without leaning on git. The store is
session-scoped; persistence is a future extension.
"""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True)
class EditSnapshot:
    """Single recorded file edit."""
    path: str
    before: str
    after: str
    ts: float

    def to_revert_content(self) -> str:
        """Content to write back when undoing this edit."""
        return self.before


class EditSnapshotStore:
    """Bounded FIFO store for edit snapshots."""

    def __init__(self, max_size: int = 200) -> None:
        self._max = max_size
        self._entries: list[EditSnapshot] = []

    def record(self, *, path: str, before: str, after: str) -> EditSnapshot:
        snap = EditSnapshot(
            path=path, before=before, after=after, ts=time.time(),
        )
        self._entries.append(snap)
        if len(self._entries) > self._max:
            # Drop oldest entries to stay under the cap.
            overflow = len(self._entries) - self._max
            del self._entries[:overflow]
        return snap

    def list(self) -> tuple[EditSnapshot, ...]:
        return tuple(self._entries)

    def recent(self, n: int) -> tuple[EditSnapshot, ...]:
        if n <= 0:
            return ()
        return tuple(reversed(self._entries[-n:]))

    def for_path(self, path: str) -> tuple[EditSnapshot, ...]:
        return tuple(s for s in self._entries if s.path == path)

    def pop_latest(self) -> EditSnapshot | None:
        if not self._entries:
            return None
        return self._entries.pop()

    def clear(self) -> None:
        self._entries.clear()
