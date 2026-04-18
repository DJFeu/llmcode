"""Nested memory path tracker (M4).

Claude Code's ``findRelevantMemories`` walks parent directories for
``MEMORY.md`` entries and attaches them. Without a tracker the
runtime risks re-loading the same file repeatedly or forgetting
which trigger already fired. This tiny set-based tracker gives the
existing memory layer a place to record "seen" and "loaded" state.
"""
from __future__ import annotations


class NestedMemoryTracker:
    def __init__(self) -> None:
        self._attached: set[str] = set()
        self._loaded: set[str] = set()

    @property
    def attached(self) -> set[str]:
        return set(self._attached)

    @property
    def loaded(self) -> set[str]:
        return set(self._loaded)

    def register_attachment(self, path: str) -> None:
        self._attached.add(path)

    def register_load(self, path: str) -> None:
        self._loaded.add(path)

    def report(self) -> dict:
        return {
            "attached": len(self._attached),
            "loaded": len(self._loaded),
            "pending": len(self._attached - self._loaded),
        }
