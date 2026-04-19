"""Mode-specific system reminders (plan / build-switch / max-steps).

Reference-aligned with opencode (packages/opencode/src/session/prompt/):
they added four mode-specific reminders injected dynamically at turn
boundaries — orthogonal to the model-specific prompt file. We ship
the same four as reusable builder functions so callers can assemble
the right reminder for the active session.

Scope for this commit:

    * Four template files under ``prompts/mode/``.
    * Four builder functions returning the rendered reminder text.
    * Plan-mode reminder auto-injected into the system prompt when
      ``PermissionMode.PLAN`` is the active mode (the wire-in part).

Max-steps injection / build-switch transition hooks are invoked by
the runtime from call sites we wire up in a follow-up — the builder
functions land first so callers can test against a stable API.
"""
from __future__ import annotations

from llm_code.runtime.permissions import PermissionMode, PermissionPolicy
from llm_code.runtime.prompt import SystemPromptBuilder
from llm_code.runtime.context import ProjectContext
from llm_code.runtime.prompt_mode_reminders import (
    build_switch_reminder,
    max_steps_reminder,
    plan_mode_reminder,
    plan_mode_reminder_anthropic,
)


class TestPlanModeReminder:
    def test_contains_plan_mode_header(self) -> None:
        text = plan_mode_reminder()
        assert "Plan Mode" in text
        assert "<system-reminder>" in text
        assert "</system-reminder>" in text

    def test_warns_against_mutations(self) -> None:
        """The reminder must explicitly forbid edits / writes / commits."""
        text = plan_mode_reminder()
        lowered = text.lower()
        assert "edit" in lowered or "modify" in lowered
        assert "read-only" in lowered or "readonly" in lowered

    def test_plan_file_placeholder_filled_when_provided(self, tmp_path) -> None:
        plan_path = tmp_path / "plan.md"
        text = plan_mode_reminder(plan_file=str(plan_path))
        assert str(plan_path) in text

    def test_plan_file_omitted_gracefully(self) -> None:
        """When no plan file is provided, the reminder must still render
        without a stray ``{plan_file}`` literal left behind."""
        text = plan_mode_reminder(plan_file=None)
        assert "{plan_file}" not in text


class TestPlanModeReminderAnthropic:
    def test_contains_workflow_phases(self) -> None:
        """The Anthropic variant includes the enhanced planning workflow
        (phases 1-5 in opencode's reference)."""
        text = plan_mode_reminder_anthropic()
        assert "Phase 1" in text or "phase 1" in text.lower()
        assert "ExitPlanMode" in text

    def test_anthropic_variant_is_distinct_from_default(self) -> None:
        """Ensure we don't ship two identical templates under different
        names — each reminder has its own shape."""
        assert plan_mode_reminder_anthropic() != plan_mode_reminder()


class TestBuildSwitchReminder:
    def test_announces_mode_change(self) -> None:
        text = build_switch_reminder()
        assert "plan" in text.lower()
        assert "build" in text.lower()
        assert "<system-reminder>" in text


class TestMaxStepsReminder:
    def test_forbids_further_tool_calls(self) -> None:
        text = max_steps_reminder()
        lowered = text.lower()
        assert "maximum" in lowered or "max" in lowered
        assert "step" in lowered or "iteration" in lowered or "tool" in lowered

    def test_work_done_placeholder_filled(self) -> None:
        text = max_steps_reminder(work_done="renamed three files")
        assert "renamed three files" in text

    def test_no_orphan_placeholders(self) -> None:
        """No stray ``{work_done}`` / ``{remaining}`` literals when the
        caller omits those fields."""
        text = max_steps_reminder()
        assert "{work_done}" not in text
        assert "{remaining}" not in text


class TestSystemPromptBuilderPlanWire:
    """SystemPromptBuilder should auto-inject the plan-mode reminder
    when the caller hands it a ``PermissionPolicy`` in PLAN mode."""

    def test_plan_mode_injects_reminder(self, tmp_path) -> None:
        builder = SystemPromptBuilder()
        ctx = ProjectContext(cwd=tmp_path, is_git_repo=False, git_status="", instructions="")
        policy = PermissionPolicy(mode=PermissionMode.PLAN)

        prompt = builder.build(ctx, permission_policy=policy)

        assert "Plan Mode" in prompt
        assert "read-only" in prompt.lower()

    def test_non_plan_mode_omits_reminder(self, tmp_path) -> None:
        builder = SystemPromptBuilder()
        ctx = ProjectContext(cwd=tmp_path, is_git_repo=False, git_status="", instructions="")
        policy = PermissionPolicy(mode=PermissionMode.WORKSPACE_WRITE)

        prompt = builder.build(ctx, permission_policy=policy)

        assert "Plan Mode - System Reminder" not in prompt

    def test_no_policy_omits_reminder(self, tmp_path) -> None:
        """Callers that never opted into the wire (no permission_policy
        passed) should still get the old prompt unchanged."""
        builder = SystemPromptBuilder()
        ctx = ProjectContext(cwd=tmp_path, is_git_repo=False, git_status="", instructions="")

        prompt = builder.build(ctx)

        assert "Plan Mode - System Reminder" not in prompt
