"""Structured record of denied tool calls (H11 — Sprint 1).

Whenever a ``canUseTool``-style check vetoes a tool call, the runtime
should append a :class:`DeniedToolCall` here. The tracker is held on the
``ConversationRuntime`` (one per session) and surfaces through:

    * ``/diagnose`` output — see recent denials at a glance
    * SDK responses — callers can learn which calls their policy blocked
    * Enterprise audit logs — ``as_report()`` is JSON-safe

Non-goals
    * We don't replay or retry denied calls — that's the caller's choice.
    * We don't persist across sessions — the tracker lives as long as the
      runtime it belongs to.

This module is intentionally small; the tool-pipeline integration lands
in a follow-up ticket so this change stays review-friendly.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class DenialSource(Enum):
    """Where the veto came from."""
    POLICY = "policy"   # permission policy / 6-stage gate
    HOOK = "hook"       # PreToolUse / custom hook returned deny
    USER = "user"       # interactive user rejection
    SANDBOX = "sandbox" # sandbox refused (e.g. bwrap denial)
    OTHER = "other"     # catch-all


@dataclass(frozen=True)
class DeniedToolCall:
    """A single denied tool invocation.

    ``input`` is stored verbatim so the tool args can be inspected in a
    follow-up report. Callers should make sure the dict is JSON-safe
    before passing it in — the tracker does not sanitise binary blobs.
    """
    tool_name: str
    tool_use_id: str
    input: dict
    reason: str
    source: DenialSource
    # Defaults to "now" so call sites don't need to thread a clock in.
    denied_at: float = field(default_factory=time.time)


class PermissionDenialTracker:
    """Append-only log of :class:`DeniedToolCall` entries.

    Use :meth:`record` at every denial site; read via :meth:`entries`
    / :meth:`as_report` / :meth:`filter_by_tool` / :meth:`recent`.
    """

    __slots__ = ("_entries",)

    def __init__(self) -> None:
        self._entries: list[DeniedToolCall] = []

    # -- Recording ------------------------------------------------------

    def record(
        self,
        tool_name: str,
        tool_use_id: str,
        input: dict,
        reason: str,
        source: DenialSource,
    ) -> DeniedToolCall:
        entry = DeniedToolCall(
            tool_name=tool_name,
            tool_use_id=tool_use_id,
            input=dict(input),  # defensive copy — frozen dataclass can't
                                # rely on the caller keeping the dict stable
            reason=reason,
            source=source,
        )
        self._entries.append(entry)
        return entry

    def clear(self) -> None:
        self._entries.clear()

    # -- Reading --------------------------------------------------------

    @property
    def count(self) -> int:
        return len(self._entries)

    def entries(self) -> tuple[DeniedToolCall, ...]:
        """Return an immutable snapshot of all denials in insertion order."""
        return tuple(self._entries)

    def filter_by_tool(self, tool_name: str) -> tuple[DeniedToolCall, ...]:
        return tuple(e for e in self._entries if e.tool_name == tool_name)

    def recent(self, n: int) -> tuple[DeniedToolCall, ...]:
        if n <= 0:
            return ()
        return tuple(self._entries[-n:])

    def as_report(self) -> dict:
        """JSON-safe aggregate for ``/diagnose`` and audit logs."""
        by_tool: dict[str, int] = {}
        by_source: dict[str, int] = {}
        for e in self._entries:
            by_tool[e.tool_name] = by_tool.get(e.tool_name, 0) + 1
            by_source[e.source.value] = by_source.get(e.source.value, 0) + 1
        return {
            "total": len(self._entries),
            "by_tool": by_tool,
            "by_source": by_source,
            "entries": [
                {
                    "tool_name": e.tool_name,
                    "tool_use_id": e.tool_use_id,
                    "reason": e.reason,
                    "source": e.source.value,
                    "denied_at": e.denied_at,
                }
                for e in self._entries
            ],
        }
