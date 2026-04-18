"""Tests for the task state transition table (H5a — Sprint 3).

Adds two things to ``llm_code/task/types.py``:

    * ``TaskStatus.PENDING_APPROVAL`` — waits on an external approval
      (user prompt, enterprise gate, hook). Previously this state was
      implicit inside ``DO`` which made it impossible to distinguish
      "executing" from "waiting for a human".
    * ``TaskTransition.is_valid(from_state, to_state)`` — guards the
      runtime against illegal jumps (e.g. DONE → DO). The table is
      intentionally lenient on retries from BLOCKED so operator
      recovery stays easy.
"""
from __future__ import annotations

import pytest

from llm_code.task.types import TaskStatus, TaskTransition


class TestPendingApprovalStatus:
    def test_enum_value(self) -> None:
        assert TaskStatus.PENDING_APPROVAL.value == "pending_approval"

    def test_from_string(self) -> None:
        assert TaskStatus("pending_approval") is TaskStatus.PENDING_APPROVAL

    def test_pending_approval_is_new_not_replacing(self) -> None:
        """Ensure the full pre-existing status list is still present so
        old session files keep deserialising."""
        values = {s.value for s in TaskStatus}
        assert {"plan", "do", "verify", "close", "done", "blocked"} <= values
        assert "pending_approval" in values


class TestTaskTransitionHappyPath:
    def test_plan_to_do(self) -> None:
        assert TaskTransition.is_valid(TaskStatus.PLAN, TaskStatus.DO) is True

    def test_do_to_verify(self) -> None:
        assert TaskTransition.is_valid(TaskStatus.DO, TaskStatus.VERIFY) is True

    def test_do_to_pending_approval(self) -> None:
        assert (
            TaskTransition.is_valid(TaskStatus.DO, TaskStatus.PENDING_APPROVAL)
            is True
        )

    def test_pending_approval_back_to_do(self) -> None:
        """Approval granted → resume execution."""
        assert (
            TaskTransition.is_valid(TaskStatus.PENDING_APPROVAL, TaskStatus.DO)
            is True
        )

    def test_pending_approval_to_blocked(self) -> None:
        """Approval denied → blocked."""
        assert (
            TaskTransition.is_valid(TaskStatus.PENDING_APPROVAL, TaskStatus.BLOCKED)
            is True
        )

    def test_verify_to_close(self) -> None:
        assert TaskTransition.is_valid(TaskStatus.VERIFY, TaskStatus.CLOSE) is True

    def test_close_to_done(self) -> None:
        assert TaskTransition.is_valid(TaskStatus.CLOSE, TaskStatus.DONE) is True


class TestTaskTransitionIllegalJumps:
    def test_done_is_terminal(self) -> None:
        for target in TaskStatus:
            if target is TaskStatus.DONE:
                continue
            assert TaskTransition.is_valid(TaskStatus.DONE, target) is False, (
                f"DONE should be terminal; allowed transition to {target}"
            )

    def test_cannot_skip_verify(self) -> None:
        """DO → CLOSE without verifying should be rejected."""
        assert TaskTransition.is_valid(TaskStatus.DO, TaskStatus.CLOSE) is False

    def test_plan_cannot_jump_to_verify(self) -> None:
        assert TaskTransition.is_valid(TaskStatus.PLAN, TaskStatus.VERIFY) is False


class TestTaskTransitionBlockedRecovery:
    """BLOCKED should reach all active states so operators can resume
    after unblocking (e.g. fixing a dep, granting access, etc.)."""

    @pytest.mark.parametrize(
        "target",
        [TaskStatus.PLAN, TaskStatus.DO, TaskStatus.VERIFY],
    )
    def test_blocked_can_resume(self, target: TaskStatus) -> None:
        assert TaskTransition.is_valid(TaskStatus.BLOCKED, target) is True

    def test_blocked_to_done_still_forbidden(self) -> None:
        """Can't declare DONE straight from BLOCKED — you have to go
        through at least VERIFY → CLOSE first so the verify evidence
        gets recorded."""
        assert TaskTransition.is_valid(TaskStatus.BLOCKED, TaskStatus.DONE) is False


class TestSelfTransitionIgnored:
    """Same-state 'transition' is a no-op and always legal so callers
    don't have to special-case it."""

    @pytest.mark.parametrize("state", list(TaskStatus))
    def test_same_state(self, state: TaskStatus) -> None:
        assert TaskTransition.is_valid(state, state) is True


class TestSerializationBackwardCompat:
    def test_old_status_values_still_round_trip(self) -> None:
        """Old session files wrote ``"do"`` / ``"plan"`` / ... — each
        must still resolve to a member, even after PENDING_APPROVAL
        got added."""
        for raw in ("plan", "do", "verify", "close", "done", "blocked"):
            assert TaskStatus(raw).value == raw


class TestAllowedTargetsHelper:
    def test_allowed_targets_returns_iterable(self) -> None:
        targets = TaskTransition.allowed_targets(TaskStatus.DO)
        assert TaskStatus.VERIFY in targets
        assert TaskStatus.PENDING_APPROVAL in targets
        assert TaskStatus.DONE not in targets

    def test_done_has_no_targets(self) -> None:
        """Terminal — only self-transition is allowed, and that's
        handled by :meth:`is_valid`, not by the targets list."""
        targets = TaskTransition.allowed_targets(TaskStatus.DONE)
        assert targets == frozenset()
