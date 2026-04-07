"""Task state machine with terminal-state guards for message injection.

Extends the in-flight task model with an explicit lifecycle
(PENDING -> RUNNING -> COMPLETED | FAILED | CANCELLED) so callers can reject
``inject_message`` into dead or finished tasks instead of silently dropping
the payload.

This is intentionally independent of :class:`AsyncTaskRegistry` (which owns
asyncio.Task lifetimes) — task-state bookkeeping is orthogonal to the
underlying coroutine handle and survives past the task's completion.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable


class TaskState(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES: frozenset[TaskState] = frozenset({
    TaskState.COMPLETED,
    TaskState.FAILED,
    TaskState.CANCELLED,
})


# Allowed transitions: from_state -> set of allowed to_states
_ALLOWED: dict[TaskState, frozenset[TaskState]] = {
    TaskState.PENDING: frozenset({TaskState.RUNNING, TaskState.CANCELLED, TaskState.FAILED}),
    TaskState.RUNNING: frozenset({TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.COMPLETED: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.CANCELLED: frozenset(),
}


class TaskTerminalError(RuntimeError):
    """Raised when attempting to inject into a task already in a terminal state."""


class InvalidStateTransition(RuntimeError):
    """Raised on an illegal state transition (e.g. RUNNING -> PENDING)."""


@dataclass
class _TaskRecord:
    task_id: str
    state: TaskState = TaskState.PENDING
    inbox: list[dict] = field(default_factory=list)


class TaskStateMachine:
    """Thread-safe task lifecycle registry with injection guards."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks: dict[str, _TaskRecord] = {}

    def create(self, task_id: str) -> TaskState:
        with self._lock:
            if task_id in self._tasks:
                raise ValueError(f"task {task_id!r} already exists")
            self._tasks[task_id] = _TaskRecord(task_id=task_id)
            return TaskState.PENDING

    def get_state(self, task_id: str) -> TaskState:
        with self._lock:
            rec = self._tasks.get(task_id)
            if rec is None:
                raise KeyError(task_id)
            return rec.state

    def is_terminal(self, task_id: str) -> bool:
        with self._lock:
            rec = self._tasks.get(task_id)
            if rec is None:
                return True  # unknown == terminal for safety
            return rec.state in TERMINAL_STATES

    def transition(self, task_id: str, new_state: TaskState) -> TaskState:
        """Move *task_id* to *new_state*. Raises on unknown id or illegal jump."""
        with self._lock:
            rec = self._tasks.get(task_id)
            if rec is None:
                raise KeyError(task_id)
            if new_state == rec.state:
                return rec.state
            allowed = _ALLOWED[rec.state]
            if new_state not in allowed:
                raise InvalidStateTransition(
                    f"cannot transition {task_id!r}: {rec.state.value} -> {new_state.value}"
                )
            rec.state = new_state
            return new_state

    def inject_message(self, task_id: str, msg: dict) -> None:
        """Queue a message for *task_id*.

        Raises :class:`TaskTerminalError` if the task is already finished or
        does not exist — callers must not quietly drop messages into dead tasks.
        """
        with self._lock:
            rec = self._tasks.get(task_id)
            if rec is None or rec.state in TERMINAL_STATES:
                raise TaskTerminalError(
                    f"task {task_id!r} is terminal; cannot inject message"
                )
            rec.inbox.append(dict(msg))

    def drain_messages(self, task_id: str) -> list[dict]:
        with self._lock:
            rec = self._tasks.get(task_id)
            if rec is None:
                return []
            drained = list(rec.inbox)
            rec.inbox.clear()
            return drained

    def cleanup_terminal(self) -> list[str]:
        """Remove all terminal-state task records. Returns removed ids."""
        with self._lock:
            removed = [
                tid for tid, rec in self._tasks.items()
                if rec.state in TERMINAL_STATES
            ]
            for tid in removed:
                del self._tasks[tid]
        return removed

    def all_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._tasks.keys())

    def ids_in_state(self, states: Iterable[TaskState]) -> tuple[str, ...]:
        target = set(states)
        with self._lock:
            return tuple(tid for tid, rec in self._tasks.items() if rec.state in target)
