"""Wave2-4: Compaction Todo Preserver.

Snapshots incomplete tasks immediately before a compaction pass and makes
the snapshot available to hooks as the ``pre_compact`` / ``post_compact``
payload. The long-lived task state itself already lives on disk in
``TaskLifecycleManager``, so the next turn's prompt rebuild will pick it
back up via ``build_incomplete_tasks_prompt`` regardless. The value this
module adds is:

1. Hook observers can distinguish the compaction phases and react
   (e.g. persist the pre-compact state to an audit log, or decide
   whether to veto the compaction).
2. A formatted reminder string with a hard token cap so the injection
   path on the *next* turn cannot balloon an already-tight context
   window.

The preserver is deliberately pure: it takes a ``TaskLifecycleManager``
(or any object with a compatible ``list_tasks`` method) and returns
plain data. No hidden I/O, no coupling to conversation state.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

# Rough chars-per-token heuristic. The runtime uses the same approximation
# in other "estimated token" paths; keeping it local here avoids a
# circular import with session/compressor code.
_CHARS_PER_TOKEN = 4

# Default cap for the injected reminder. 500 tokens ≈ 2000 chars is
# enough for ~30 short task titles while still leaving room after a
# compaction trim.
DEFAULT_TODO_REMINDER_TOKEN_CAP = 500


@dataclass(frozen=True)
class TodoSnapshot:
    """An immutable capture of the incomplete-task state at compact time."""

    task_id: str
    status: str
    title: str


class _TaskManagerLike(Protocol):
    """Minimal duck-typed interface — we only need list_tasks()."""

    def list_tasks(self, *, exclude_done: bool = False) -> tuple: ...  # pragma: no cover


def snapshot_incomplete_tasks(
    task_manager: _TaskManagerLike | None,
) -> tuple[TodoSnapshot, ...]:
    """Return an immutable snapshot of every not-yet-done task.

    Missing or errored task managers return an empty tuple rather than
    raising, because a compaction pass must never fail on best-effort
    observability metadata.
    """
    if task_manager is None:
        return ()
    try:
        tasks = task_manager.list_tasks(exclude_done=True)
    except Exception:  # noqa: BLE001 — defensive: observability must not break compact
        return ()
    return tuple(
        TodoSnapshot(
            task_id=getattr(t, "id", ""),
            status=getattr(getattr(t, "status", None), "value", "") or str(getattr(t, "status", "")),
            title=getattr(t, "title", ""),
        )
        for t in tasks
    )


def format_todo_reminder(
    snapshot: tuple[TodoSnapshot, ...],
    *,
    max_tokens: int = DEFAULT_TODO_REMINDER_TOKEN_CAP,
) -> str:
    """Render a snapshot as a short markdown reminder, capped at *max_tokens*.

    Returns an empty string when there are no incomplete tasks so the
    caller can trivially skip injection. The cap is a hard upper bound:
    when the rendered body would exceed it, the tail is replaced with
    ``... (N more)`` and the truncation is guaranteed under budget.
    """
    if not snapshot:
        return ""

    header = "**Incomplete tasks preserved across compaction:**"
    lines: list[str] = [header]
    char_cap = max(max_tokens * _CHARS_PER_TOKEN, len(header) + 32)

    running_len = len(header)
    for idx, item in enumerate(snapshot):
        line = f"- {item.task_id} [{item.status}]: {item.title}"
        # Reserve space for a possible truncation footer before committing
        # the next line, so the cap is a true upper bound.
        remaining = len(snapshot) - idx
        footer_reserve = len(f"\n... ({remaining} more)") if remaining > 1 else 0
        if running_len + 1 + len(line) + footer_reserve > char_cap:
            lines.append(f"... ({remaining} more)")
            break
        lines.append(line)
        running_len += 1 + len(line)

    return "\n".join(lines)
