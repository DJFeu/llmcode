"""Compaction progress label.

Compaction in llm-code currently runs as an atomic step (no incremental
events), so the label simply renders 'Compacting context…' while active.
If the runtime later exposes (current, total) progress events, the
`update(current, total)` method already handles the formatted output.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CompactionProgress:
    active: bool = False
    current: int = 0
    total: int = 0

    def start(self, total: int = 0) -> None:
        self.active = True
        self.current = 0
        self.total = total

    def update(self, current: int, total: int) -> None:
        self.active = True
        self.current = current
        self.total = total

    def stop(self) -> None:
        self.active = False
        self.current = 0
        self.total = 0

    def label(self) -> str:
        if not self.active:
            return ""
        if self.total > 0:
            return f"Compacting context: {self.current}/{self.total} messages"
        return "Compacting context…"
