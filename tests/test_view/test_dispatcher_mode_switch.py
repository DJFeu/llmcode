"""Verify the ``/mode`` and ``/yolo`` slash commands route through
:meth:`PermissionPolicy.switch_to` so the ``ModeTransition`` event is
recorded for :class:`SystemPromptBuilder` to consume on the next turn.

Previous behaviour mutated ``policy._mode`` directly which skipped the
transition bookkeeping — the ``build-switch`` reminder could never
fire because no-one noticed the flip.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from llm_code.runtime.permissions import PermissionMode, PermissionPolicy
from llm_code.view.dispatcher import CommandDispatcher


@pytest.fixture
def dispatcher_with_policy():
    """Minimal dispatcher with a real ``PermissionPolicy`` on a fake runtime."""
    policy = PermissionPolicy(mode=PermissionMode.PROMPT)

    fake_runtime = MagicMock()
    fake_runtime._permissions = policy
    fake_runtime.plan_mode = False

    state = SimpleNamespace(
        runtime=fake_runtime,
        plan_mode=False,
        config=None,
        cwd=None,
    )

    view = MagicMock()
    renderer = MagicMock()
    dispatcher = CommandDispatcher(view=view, state=state, renderer=renderer)
    return dispatcher, policy, view


class TestCmdModeRecordsTransition:
    def test_switch_to_plan_records_transition(self, dispatcher_with_policy) -> None:
        dispatcher, policy, _ = dispatcher_with_policy
        dispatcher._cmd_mode("plan")

        event = policy.last_transition()
        assert event is not None
        assert event.from_mode is PermissionMode.PROMPT
        assert event.to_mode is PermissionMode.PLAN
        assert policy.mode is PermissionMode.PLAN

    def test_switch_to_normal_from_plan_records_transition(
        self, dispatcher_with_policy,
    ) -> None:
        dispatcher, policy, _ = dispatcher_with_policy
        dispatcher._cmd_mode("plan")
        policy.consume_last_transition()  # drain the first event

        dispatcher._cmd_mode("normal")
        event = policy.last_transition()
        assert event is not None
        assert event.from_mode is PermissionMode.PLAN
        assert event.to_mode is PermissionMode.WORKSPACE_WRITE

    def test_switch_to_same_mode_records_no_transition(
        self, dispatcher_with_policy,
    ) -> None:
        dispatcher, policy, _ = dispatcher_with_policy
        dispatcher._cmd_mode("suggest")  # already PROMPT == suggest
        assert policy.last_transition() is None


class TestCmdYoloRecordsTransition:
    def test_yolo_on_records_transition(self, dispatcher_with_policy) -> None:
        dispatcher, policy, _ = dispatcher_with_policy
        dispatcher._cmd_yolo("")

        event = policy.last_transition()
        assert event is not None
        assert event.to_mode is PermissionMode.AUTO_ACCEPT

    def test_yolo_toggle_off_records_transition(self, dispatcher_with_policy) -> None:
        dispatcher, policy, _ = dispatcher_with_policy
        dispatcher._cmd_yolo("")  # ON
        policy.consume_last_transition()
        dispatcher._cmd_yolo("")  # OFF

        event = policy.last_transition()
        assert event is not None
        assert event.from_mode is PermissionMode.AUTO_ACCEPT
        assert event.to_mode is PermissionMode.PROMPT
