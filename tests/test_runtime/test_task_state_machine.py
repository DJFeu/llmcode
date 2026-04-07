"""Tests for TaskStateMachine lifecycle + terminal-state injection guards."""
from __future__ import annotations

import pytest

from llm_code.runtime.task_state_machine import (
    InvalidStateTransition,
    TERMINAL_STATES,
    TaskState,
    TaskStateMachine,
    TaskTerminalError,
)


class TestLifecycle:
    def test_create_pending(self) -> None:
        m = TaskStateMachine()
        assert m.create("t1") == TaskState.PENDING
        assert m.get_state("t1") == TaskState.PENDING

    def test_duplicate_create_raises(self) -> None:
        m = TaskStateMachine()
        m.create("t1")
        with pytest.raises(ValueError):
            m.create("t1")

    def test_happy_path_pending_running_completed(self) -> None:
        m = TaskStateMachine()
        m.create("t1")
        assert m.transition("t1", TaskState.RUNNING) == TaskState.RUNNING
        assert m.transition("t1", TaskState.COMPLETED) == TaskState.COMPLETED
        assert m.is_terminal("t1")

    def test_cancel_from_pending(self) -> None:
        m = TaskStateMachine()
        m.create("t1")
        m.transition("t1", TaskState.CANCELLED)
        assert m.is_terminal("t1")

    def test_illegal_transition_rejected(self) -> None:
        m = TaskStateMachine()
        m.create("t1")
        m.transition("t1", TaskState.RUNNING)
        m.transition("t1", TaskState.COMPLETED)
        with pytest.raises(InvalidStateTransition):
            m.transition("t1", TaskState.RUNNING)
        with pytest.raises(InvalidStateTransition):
            m.transition("t1", TaskState.PENDING)

    def test_unknown_task_raises(self) -> None:
        m = TaskStateMachine()
        with pytest.raises(KeyError):
            m.transition("nope", TaskState.RUNNING)
        with pytest.raises(KeyError):
            m.get_state("nope")


class TestInjectionGuards:
    def test_inject_running_ok(self) -> None:
        m = TaskStateMachine()
        m.create("t1")
        m.transition("t1", TaskState.RUNNING)
        m.inject_message("t1", {"role": "user", "content": "hi"})
        assert m.drain_messages("t1") == [{"role": "user", "content": "hi"}]

    def test_inject_pending_ok(self) -> None:
        m = TaskStateMachine()
        m.create("t1")
        m.inject_message("t1", {"x": 1})
        assert m.drain_messages("t1") == [{"x": 1}]

    @pytest.mark.parametrize("terminal", list(TERMINAL_STATES))
    def test_inject_terminal_rejected(self, terminal: TaskState) -> None:
        m = TaskStateMachine()
        m.create("t1")
        # Need to pass through RUNNING for COMPLETED; FAILED/CANCELLED allowed from PENDING
        if terminal == TaskState.COMPLETED:
            m.transition("t1", TaskState.RUNNING)
        m.transition("t1", terminal)
        with pytest.raises(TaskTerminalError):
            m.inject_message("t1", {"x": 1})

    def test_inject_unknown_rejected(self) -> None:
        m = TaskStateMachine()
        with pytest.raises(TaskTerminalError):
            m.inject_message("missing", {})

    def test_is_terminal_unknown_returns_true(self) -> None:
        assert TaskStateMachine().is_terminal("nope") is True


class TestCleanup:
    def test_cleanup_removes_only_terminal(self) -> None:
        m = TaskStateMachine()
        m.create("a")
        m.create("b")
        m.create("c")
        m.transition("a", TaskState.RUNNING)
        m.transition("b", TaskState.CANCELLED)  # terminal
        removed = m.cleanup_terminal()
        assert removed == ["b"]
        assert set(m.all_ids()) == {"a", "c"}

    def test_ids_in_state(self) -> None:
        m = TaskStateMachine()
        m.create("a")
        m.create("b")
        m.transition("b", TaskState.RUNNING)
        assert set(m.ids_in_state([TaskState.RUNNING])) == {"b"}
        assert set(m.ids_in_state([TaskState.PENDING])) == {"a"}
