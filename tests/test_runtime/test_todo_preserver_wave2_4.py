"""Wave2-4: Compaction todo preserver — snapshot + hook phase split.

The preserver must:

1. Take a best-effort snapshot of incomplete tasks immediately before a
   compaction pass (never raising, because observability must not break
   the compaction itself).
2. Format that snapshot into a token-capped reminder.
3. Expose ``pre_compact`` / ``post_compact`` events (in addition to the
   legacy ``session_compact`` which stays for back-compat).

These tests target the pure ``todo_preserver`` module directly plus the
hook registration map. The full integration with ``ConversationRuntime``
(all 4 compress call sites fire through the helper) is pinned indirectly
by the existing ``test_conversation*`` suites which must still pass.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from llm_code.runtime.hooks import _EVENT_GROUP, _event_matches
from llm_code.runtime.todo_preserver import (
    DEFAULT_TODO_REMINDER_TOKEN_CAP,
    TodoSnapshot,
    format_todo_reminder,
    snapshot_incomplete_tasks,
)


# ---------- Minimal fake task manager ----------

@dataclass(frozen=True)
class _FakeStatus:
    value: str


@dataclass(frozen=True)
class _FakeTask:
    id: str
    title: str
    status: _FakeStatus


class _FakeTaskManager:
    def __init__(self, tasks: tuple[_FakeTask, ...]) -> None:
        self._tasks = tasks
        self.calls: list[bool] = []

    def list_tasks(self, *, exclude_done: bool = False) -> tuple[_FakeTask, ...]:
        self.calls.append(exclude_done)
        return self._tasks


class _ExplodingTaskManager:
    """Simulates a broken on-disk task store. The preserver must
    swallow the error so a compaction pass still runs."""

    def list_tasks(self, *, exclude_done: bool = False) -> tuple:
        raise RuntimeError("task store unreadable")


# ---------- snapshot_incomplete_tasks ----------

def test_snapshot_returns_empty_when_manager_is_none() -> None:
    assert snapshot_incomplete_tasks(None) == ()


def test_snapshot_requests_exclude_done() -> None:
    """Done tasks should never leak into the preserved set — the
    incomplete-task list is what survives across the compaction."""
    tm = _FakeTaskManager(tasks=())
    snapshot_incomplete_tasks(tm)
    assert tm.calls == [True]


def test_snapshot_captures_id_status_title() -> None:
    tm = _FakeTaskManager(
        tasks=(
            _FakeTask(id="task-0001", title="Ship wave2-4", status=_FakeStatus("DO")),
            _FakeTask(id="task-0002", title="Review PR", status=_FakeStatus("PLAN")),
        ),
    )
    snap = snapshot_incomplete_tasks(tm)
    assert len(snap) == 2
    assert snap[0] == TodoSnapshot(task_id="task-0001", status="DO", title="Ship wave2-4")
    assert snap[1] == TodoSnapshot(task_id="task-0002", status="PLAN", title="Review PR")


def test_snapshot_swallows_errors_from_broken_manager() -> None:
    """A compaction must never fail because observability blew up."""
    assert snapshot_incomplete_tasks(_ExplodingTaskManager()) == ()


# ---------- format_todo_reminder ----------

def test_format_empty_snapshot_returns_empty_string() -> None:
    """Callers rely on the empty-string shortcut to skip injection."""
    assert format_todo_reminder(()) == ""


def test_format_small_snapshot_includes_every_task() -> None:
    snap = (
        TodoSnapshot(task_id="task-0001", status="DO", title="Ship wave2-4"),
        TodoSnapshot(task_id="task-0002", status="PLAN", title="Review PR"),
    )
    out = format_todo_reminder(snap)
    assert "task-0001" in out and "Ship wave2-4" in out
    assert "task-0002" in out and "Review PR" in out
    assert "Incomplete tasks preserved across compaction" in out


def test_format_truncates_under_hard_token_cap() -> None:
    """A giant snapshot must never overflow the cap, even by one char.
    The tail is replaced with a ``... (N more)`` footer so the user
    knows there was truncation rather than silently losing tasks."""
    many = tuple(
        TodoSnapshot(
            task_id=f"task-{i:04d}",
            status="DO",
            title="x" * 80,  # wide titles to blow the budget fast
        )
        for i in range(200)
    )
    out = format_todo_reminder(many, max_tokens=100)  # ~400 char budget
    # Hard upper bound on char count (the helper uses 4 chars/token).
    assert len(out) <= 100 * 4
    assert "more)" in out  # truncation footer present


def test_format_default_cap_is_generous_enough_for_typical_sessions() -> None:
    """Sanity check: a realistic 20-task session with ~50-char titles
    should fit comfortably under the default cap without truncation."""
    snap = tuple(
        TodoSnapshot(task_id=f"t{i}", status="DO", title="Task number " + str(i) * 3)
        for i in range(20)
    )
    out = format_todo_reminder(snap, max_tokens=DEFAULT_TODO_REMINDER_TOKEN_CAP)
    assert "more)" not in out
    for i in range(20):
        assert f"t{i}" in out


# ---------- hook event registration ----------

@pytest.mark.parametrize("event", ["pre_compact", "post_compact"])
def test_phase_split_events_registered(event: str) -> None:
    """Both phase events must be in the canonical group map so the
    glob matcher can resolve ``session.*`` patterns against them."""
    assert event in _EVENT_GROUP
    assert _EVENT_GROUP[event] == f"session.{event}"


def test_session_glob_matches_new_phase_events() -> None:
    """Existing hook configs with ``session.*`` should pick up the new
    phase events automatically without any config change."""
    assert _event_matches("session.*", "pre_compact") is True
    assert _event_matches("session.*", "post_compact") is True
    assert _event_matches("session.*", "session_compact") is True  # unchanged


def test_exact_match_still_works_for_phase_events() -> None:
    """Users can also subscribe to a single phase by exact event name."""
    assert _event_matches("pre_compact", "pre_compact") is True
    assert _event_matches("pre_compact", "post_compact") is False
