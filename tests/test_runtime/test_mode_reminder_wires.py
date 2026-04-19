"""Wire the mode reminders into actual trigger points.

The previous commit landed the four reminder *builders* and auto-
injected the plan reminder when ``PermissionMode.PLAN`` was the
active mode. This commit extends the coverage so the remaining two
reminders (``build-switch`` and ``max-steps``) can actually fire:

    * ``PermissionMode.READ_ONLY`` now also triggers the plan
      reminder — READ_ONLY has the same "nothing is allowed to
      mutate" semantics, so the reminder wording is a fit.
    * ``PermissionPolicy.switch_to`` is a new transition API.
      Callers flip the mode through it instead of mutating
      ``_mode`` directly; the policy remembers the last transition
      so the next system prompt injects
      ``build_switch_reminder`` when ``(plan|read_only) → build``.
    * ``IterationBudget`` lives alongside ``AutoCompactState`` in
      ``auto_compact`` and counts tool-use iterations per turn.
      Once the budget is exhausted, the runtime can render the
      ``max-steps`` reminder with a real work-done / remaining
      summary.
"""
from __future__ import annotations

from llm_code.runtime.auto_compact import IterationBudget
from llm_code.runtime.context import ProjectContext
from llm_code.runtime.permissions import (
    ModeTransition,
    PermissionMode,
    PermissionPolicy,
)
from llm_code.runtime.prompt import SystemPromptBuilder


class TestReadOnlyTriggersPlanReminder:
    def test_read_only_injects_plan_reminder(self, tmp_path) -> None:
        builder = SystemPromptBuilder()
        ctx = ProjectContext(
            cwd=tmp_path, is_git_repo=False, git_status="", instructions="",
        )
        policy = PermissionPolicy(mode=PermissionMode.READ_ONLY)

        prompt = builder.build(ctx, permission_policy=policy)

        assert "Plan Mode" in prompt
        assert "read-only" in prompt.lower()

    def test_plan_and_read_only_both_inject(self, tmp_path) -> None:
        """Ensure adding READ_ONLY didn't break the existing PLAN path."""
        builder = SystemPromptBuilder()
        ctx = ProjectContext(
            cwd=tmp_path, is_git_repo=False, git_status="", instructions="",
        )
        plan_prompt = builder.build(
            ctx, permission_policy=PermissionPolicy(mode=PermissionMode.PLAN),
        )
        ro_prompt = builder.build(
            ctx, permission_policy=PermissionPolicy(mode=PermissionMode.READ_ONLY),
        )
        assert "Plan Mode" in plan_prompt
        assert "Plan Mode" in ro_prompt


class TestModeTransition:
    def test_switch_to_records_transition(self) -> None:
        policy = PermissionPolicy(mode=PermissionMode.PLAN)
        event = policy.switch_to(PermissionMode.WORKSPACE_WRITE)

        assert isinstance(event, ModeTransition)
        assert event.from_mode is PermissionMode.PLAN
        assert event.to_mode is PermissionMode.WORKSPACE_WRITE
        assert policy.mode is PermissionMode.WORKSPACE_WRITE

    def test_last_transition_available_until_consumed(self) -> None:
        policy = PermissionPolicy(mode=PermissionMode.PLAN)
        policy.switch_to(PermissionMode.FULL_ACCESS)

        assert policy.last_transition() is not None
        # consume — next read should be None so the reminder only fires
        # once per transition.
        assert policy.consume_last_transition() is not None
        assert policy.consume_last_transition() is None

    def test_no_transition_when_target_matches_current(self) -> None:
        policy = PermissionPolicy(mode=PermissionMode.PLAN)
        same = policy.switch_to(PermissionMode.PLAN)
        assert same is None
        assert policy.last_transition() is None


class TestBuildSwitchInjection:
    def test_plan_to_build_injects_build_switch_reminder(self, tmp_path) -> None:
        builder = SystemPromptBuilder()
        ctx = ProjectContext(
            cwd=tmp_path, is_git_repo=False, git_status="", instructions="",
        )
        policy = PermissionPolicy(mode=PermissionMode.PLAN)
        policy.switch_to(PermissionMode.WORKSPACE_WRITE)

        prompt = builder.build(ctx, permission_policy=policy)

        # The reminder renders once per transition, then the policy
        # clears it so subsequent builds don't spam the reminder.
        assert "operational mode has changed from plan to build" in prompt
        second = builder.build(ctx, permission_policy=policy)
        assert "operational mode has changed from plan to build" not in second

    def test_read_only_to_build_also_injects(self, tmp_path) -> None:
        builder = SystemPromptBuilder()
        ctx = ProjectContext(
            cwd=tmp_path, is_git_repo=False, git_status="", instructions="",
        )
        policy = PermissionPolicy(mode=PermissionMode.READ_ONLY)
        policy.switch_to(PermissionMode.FULL_ACCESS)

        prompt = builder.build(ctx, permission_policy=policy)
        assert "operational mode has changed" in prompt

    def test_unrelated_transition_does_not_inject(self, tmp_path) -> None:
        """prompt → workspace_write isn't a plan→build flip, so no
        build-switch reminder."""
        builder = SystemPromptBuilder()
        ctx = ProjectContext(
            cwd=tmp_path, is_git_repo=False, git_status="", instructions="",
        )
        policy = PermissionPolicy(mode=PermissionMode.PROMPT)
        policy.switch_to(PermissionMode.WORKSPACE_WRITE)

        prompt = builder.build(ctx, permission_policy=policy)
        assert "operational mode has changed" not in prompt


class TestIterationBudget:
    def test_starts_at_zero(self) -> None:
        budget = IterationBudget(max_iterations=10)
        assert budget.used == 0
        assert budget.exceeded is False

    def test_tick_increments(self) -> None:
        budget = IterationBudget(max_iterations=3)
        budget.tick()
        budget.tick()
        assert budget.used == 2
        assert budget.exceeded is False

    def test_exceeded_when_used_reaches_max(self) -> None:
        budget = IterationBudget(max_iterations=2)
        budget.tick()
        assert budget.exceeded is False
        budget.tick()
        assert budget.exceeded is True

    def test_reset_clears_counter(self) -> None:
        budget = IterationBudget(max_iterations=2)
        budget.tick()
        budget.tick()
        budget.reset()
        assert budget.used == 0
        assert budget.exceeded is False

    def test_build_reminder_returns_max_steps_text(self) -> None:
        budget = IterationBudget(max_iterations=1)
        budget.tick()
        text = budget.build_reminder(work_done="drafted plan", remaining="run tests")
        assert "MAXIMUM STEPS REACHED" in text
        assert "drafted plan" in text
        assert "run tests" in text

    def test_build_reminder_when_not_exceeded_returns_none(self) -> None:
        budget = IterationBudget(max_iterations=5)
        budget.tick()
        assert budget.build_reminder() is None
