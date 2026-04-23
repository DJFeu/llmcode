"""Mode-specific system-reminder builders (opencode parity).

Reference: ``packages/opencode/src/session/prompt/{plan,plan-reminder-anthropic,
build-switch,max-steps}.txt``. Opencode injects these as ``<system-reminder>``
messages at turn boundaries when the session mode changes, a plan-mode guard
is active, or the per-turn tool budget is exhausted. We ship the same four
as reusable builder functions so callers can assemble the right reminder
for the active session.

Templates live under ``engine/prompts/modes/*.j2`` (moved in v12 M1 and
consolidated in M8.b). They carry ``{placeholder}`` markers — literal
Python ``str.replace`` sentinels, not Jinja2 variables — so callers can
continue to substitute dynamic context (plan file path, work-done summary,
remaining-tasks summary) after the template loads.
"""
from __future__ import annotations

from pathlib import Path

_MODE_DIR = (
    Path(__file__).resolve().parent.parent / "engine" / "prompts" / "modes"
)


def _load(template_name: str) -> str:
    """Load a mode-reminder template via the engine PromptBuilder.

    Thin delegate to :func:`~llm_code.engine.prompt_builder.render_template_file`;
    keeps the rendering path unified with Component-based callers.
    """
    from llm_code.engine.prompt_builder import render_template_file

    return render_template_file(
        f"{template_name}.j2", templates_dir=_MODE_DIR
    )


def plan_mode_reminder(plan_file: str | None = None) -> str:
    """Return the plan-mode ``<system-reminder>`` text.

    ``plan_file`` — optional path to the dedicated plan file the agent
    is permitted to edit in plan mode (the single write exception).
    When ``None``, the plan-file block is omitted gracefully so no
    ``{plan_file}`` literal leaks into the rendered text.
    """
    template = _load("plan")
    if plan_file:
        section = (
            "Your plan file is at "
            f"`{plan_file}`. You may edit this file with the Write or "
            "edit_file tool — all other mutating tools remain disallowed."
        )
    else:
        section = ""
    return template.replace("{plan_file_section}", section).rstrip() + "\n"


def plan_mode_reminder_anthropic(plan_file: str | None = None) -> str:
    """Claude-family variant with the explicit 5-phase planning workflow.

    Anthropic models follow the structured workflow better when it's
    laid out as phases with a defined exit action (``ExitPlanMode``).
    """
    template = _load("plan_anthropic")
    if plan_file:
        section = (
            f"Your plan file is at `{plan_file}`. Build the plan "
            "incrementally by writing to or editing this file. NOTE: this "
            "is the only file you are allowed to edit — all other "
            "actions must be read-only.\n\n"
            "**Plan File Guidelines:** include only the final recommended "
            "approach, not every alternative considered. Keep it "
            "comprehensive yet concise."
        )
    else:
        section = (
            "No plan file has been designated yet. Ask the user where the "
            "plan should live, or keep the plan in the conversation — do "
            "not write it to the filesystem without the user's approval."
        )
    return template.replace("{plan_file_section}", section).rstrip() + "\n"


def build_switch_reminder() -> str:
    """Notification that the session flipped from plan → build."""
    return _load("build_switch").rstrip() + "\n"


def max_steps_reminder(work_done: str = "", remaining: str = "") -> str:
    """Return the ``<system-reminder>`` for iteration-budget exhaustion.

    ``work_done`` / ``remaining`` — optional short summaries the caller
    can pass in so the reminder steers the model's final text response
    toward concrete deliverables / gaps. When omitted the sections
    render empty (no orphan ``{...}`` literal).
    """
    template = _load("max_steps")
    work_section = f"\n  Previously completed: {work_done}" if work_done else ""
    remaining_section = f"\n  Known gaps: {remaining}" if remaining else ""
    out = template.replace("{work_done_section}", work_section)
    out = out.replace("{remaining_section}", remaining_section)
    return out.rstrip() + "\n"
