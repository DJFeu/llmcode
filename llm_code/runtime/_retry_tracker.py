"""Detect idempotent tool-call retry loops.

When the model emits the same (tool_name, args) tuple twice in a row,
something is broken — either the parser is dropping args, the tool is
returning an error the model can't recover from, or the model is stuck.
Aborting beats burning the entire context window in a retry loop.
"""
from __future__ import annotations

import json
from typing import Any


class RecentToolCallTracker:
    """Per-turn tracker. Hashes the most recent (name, args) pair via
    a stable JSON representation, returns True when the same pair would
    be dispatched twice in a row.

    Lifetime: one instance per ConversationRuntime turn. Reset between
    turns by simply throwing the instance away.
    """

    __slots__ = ("_last_signature",)

    def __init__(self) -> None:
        self._last_signature: str | None = None

    def is_idempotent_retry(self, name: str, args: Any) -> bool:
        """Would dispatching ``(name, args)`` repeat the previous call?"""
        sig = self._signature(name, args)
        if sig is None:
            return False
        return sig == self._last_signature

    def record(self, name: str, args: Any) -> None:
        """Record a dispatch so the next call can compare."""
        self._last_signature = self._signature(name, args)

    @staticmethod
    def _signature(name: str, args: Any) -> str | None:
        """Stable string representation of (name, args). Returns None
        when args contain unhashable / non-JSON-serializable values
        (in which case the tracker conservatively reports 'not a retry')."""
        try:
            args_repr = json.dumps(args, sort_keys=True)
        except (TypeError, ValueError):
            return None
        return f"{name}::{args_repr}"
