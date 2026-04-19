"""System prompt builder for the conversation runtime."""
from __future__ import annotations

import dataclasses
import json
import logging
import platform
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from llm_code.api.types import ToolDefinition
from llm_code.runtime.context import ProjectContext
from llm_code.runtime.dynamic_prompt import build_delegation_section
from llm_code.runtime.prompt_guard import sanitize_mcp_instructions

if TYPE_CHECKING:
    from llm_code.runtime.indexer import ProjectIndex
    from llm_code.runtime.memory_layers import GovernanceRule
    from llm_code.runtime.skills import Skill, SkillSet
    from llm_code.task.manager import TaskLifecycleManager

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def select_intro_prompt(model: str) -> str:
    """Pick a model-tuned system intro prompt based on model name.

    Returns the file content from prompts/<family>.md, falling back to default.md.

    Routing order matters — more specific patterns must win. Reference-
    aligned with opencode's ``session/system.ts`` routing: ``copilot``
    wins over ``gpt``; reasoning-class OpenAI models (o1/o3/gpt-4/gpt-5)
    route to ``beast`` rather than the baseline ``gpt`` prompt; the
    ``trinity`` substring catches any model id the user labels as such.
    """
    if not model:
        return _read_prompt("default")

    m = model.lower()

    # Most specific backends first — copilot and codex are distinct
    # surfaces from raw OpenAI, so they must win over the generic gpt
    # branch even when their ids contain "gpt".
    if "copilot" in m:
        return _read_prompt("copilot_gpt5")
    if "codex" in m:
        return _read_prompt("codex")

    # Reasoning-class OpenAI models iterate best with the beast prompt.
    if (
        "o1" in m
        or "o3" in m
        or "gpt-4" in m or "gpt4" in m
        or "gpt-5" in m or "gpt5" in m
    ):
        return _read_prompt("beast")

    # Plain GPT (3.5 etc.) still uses the tuned gpt prompt.
    if "gpt-" in m or "/gpt" in m or m.startswith("gpt"):
        return _read_prompt("gpt")

    if "claude" in m or "anthropic" in m or "sonnet" in m or "opus" in m or "haiku" in m:
        return _read_prompt("anthropic")
    if "gemini" in m:
        return _read_prompt("gemini")
    if "trinity" in m:
        return _read_prompt("trinity")
    if "qwen" in m:
        return _read_prompt("qwen")
    if "llama" in m:
        return _read_prompt("llama")
    if "deepseek" in m:
        return _read_prompt("deepseek")
    if "kimi" in m or "moonshot" in m:
        return _read_prompt("kimi")
    return _read_prompt("default")


def _read_prompt(name: str) -> str:
    """Read a prompt file from prompts/ directory. Falls back to embedded default."""
    path = _PROMPTS_DIR / f"{name}.md"
    if path.is_file():
        try:
            return path.read_text(encoding="utf-8").rstrip()
        except OSError:
            pass
    # Fallback when file missing (e.g. during tests, partial install)
    return (
        "You are a coding assistant running inside a terminal. "
        "You have access to tools that let you read, write, and edit files, "
        "search code, and run shell commands."
    )


_INTRO = """\
You are a coding assistant running inside a terminal. \
You have access to tools that let you read, write, and edit files, \
search code, and run shell commands.\
"""

_BEHAVIOR_RULES = """\
Rules:
- NEVER output your thinking, reasoning, or analysis as text. Either call a tool or give the final answer.
- When you have enough information, answer immediately. Do NOT search again.
- After using tools, you MUST give a direct answer to the user. Do not end without responding.
- Limit tool use to 3 calls maximum per question. Then answer with what you have.
- Read code before modifying it
- Do not add features the user did not ask for
- Do not add error handling or comments unless asked
- Do not over-engineer or create unnecessary abstractions
- Three similar lines of code is better than a premature abstraction
- If something fails, diagnose why before switching approach
- Report results honestly — do not claim something works without verifying
- Keep responses concise — lead with the answer, not the reasoning
- For code changes, show the minimal diff needed
"""

_LOCAL_MODEL_RULES = """\
- Do NOT use the agent tool unless the user explicitly asks for it or the task genuinely \
requires parallel sub-tasks. For normal questions, conversations, or simple tasks, \
respond directly without spawning agents.
"""

_XML_TOOL_INSTRUCTIONS = """\
When you need to use a tool, emit exactly one JSON block wrapped in \
<tool_call>...</tool_call> XML tags — nothing else on those lines. \
The JSON must have two keys: "tool" (the tool name) and "args" (an object \
of parameters). Example:
<tool_call>{"tool": "read_file", "args": {"path": "/README.md"}}</tool_call>
Wait for the tool result before continuing.\
"""

_CACHE_BOUNDARY = "# -- CACHE BOUNDARY --"

# Cache control marker inserted between scope transitions (API-level caching)
_CACHE_CONTROL_MARKER = json.dumps({"type": "cache_control", "cache_type": "ephemeral"})

ScopeType = Literal["global", "project", "session"]


@dataclasses.dataclass(frozen=True)
class PromptSection:
    """A single section of the system prompt with scope and priority metadata.

    Scope semantics:
    - "global":  Behavior rules and tool instructions shared across all projects.
    - "project": Governance rules, project index, CLAUDE.md — shared across
                 sessions within the same project.
    - "session": Environment info, memory, active skills — per-session content.

    Priority controls ordering within the same scope (lower value = earlier).
    """

    content: str
    scope: ScopeType
    priority: int = 0


class SystemPromptBuilder:
    def build(
        self,
        context: ProjectContext,
        tools: tuple[ToolDefinition, ...] = (),
        native_tools: bool = True,
        skills: "SkillSet | None" = None,
        active_skill_content: str | None = None,
        project_index: "ProjectIndex | None" = None,
        memory_entries: dict | None = None,
        memory_summaries: list[str] | None = None,
        mcp_instructions: dict[str, str] | None = None,
        governance_rules: "tuple[GovernanceRule, ...] | None" = None,
        task_manager: "TaskLifecycleManager | None" = None,
        routed_skills: "tuple[Skill, ...] | None" = None,
        routed_skills_low_confidence: bool = False,
        is_local_model: bool = False,
        model_name: str = "",
        personas: dict | None = None,
        permission_policy: object | None = None,  # PermissionPolicy — loose typed to avoid import cycle
        plan_file: str | None = None,
    ) -> str:
        sections: list[PromptSection] = []

        # ------------------------------------------------------------------ #
        # GLOBAL scope — governance rules, behavior rules, tool instructions
        # Shared across all projects; cached at global boundary.
        # ------------------------------------------------------------------ #
        intro = select_intro_prompt(model_name) if model_name else _INTRO
        sections.append(PromptSection(content=intro, scope="global", priority=0))

        # Governance rules (L0) — injected before behavior rules
        if governance_rules:
            gov_lines = ["## Governance Rules\n"]
            categories: dict[str, list] = {}
            for rule in governance_rules:
                categories.setdefault(rule.category, []).append(rule)
            for cat, cat_rules in categories.items():
                gov_lines.append(f"### {cat.title()}")
                for r in cat_rules:
                    source_name = Path(r.source).name if r.source else "unknown"
                    gov_lines.append(f"- {r.content} _(from {source_name})_")
                gov_lines.append("")
            sections.append(PromptSection(content="\n".join(gov_lines), scope="global", priority=5))

        sections.append(PromptSection(content=_BEHAVIOR_RULES, scope="global", priority=10))

        # Personas section (Wave 2 wiring) — only rendered when personas provided.
        if personas:
            from llm_code.runtime.prompt_sections import build_personas_section
            personas_text = build_personas_section(personas)
            if personas_text:
                sections.append(PromptSection(content=personas_text, scope="global", priority=12))

        if is_local_model:
            sections.append(PromptSection(content=_LOCAL_MODEL_RULES, scope="global", priority=11))

        if not native_tools and tools:
            sections.append(PromptSection(content=_XML_TOOL_INSTRUCTIONS, scope="global", priority=20))
            tool_lines = ["Available tools:"]
            for t in tools:
                schema_str = json.dumps(t.input_schema, separators=(",", ":"))
                tool_lines.append(f"  - {t.name}: {t.description}  schema={schema_str}")
            sections.append(PromptSection(content="\n".join(tool_lines), scope="global", priority=21))

        # Active capabilities — dynamic delegation table built from live tools/skills.
        # Renders before the routed_skills wall-of-text so the table primes the model
        # with the high-level menu before each skill body is shown.
        delegation = build_delegation_section(
            tools=tools,
            skills=routed_skills or (),
            low_confidence=routed_skills_low_confidence,
        )
        if delegation:
            sections.append(PromptSection(content=delegation, scope="global", priority=25))

        # Routed skills — only the skill(s) matched by the skill router
        if routed_skills:
            auto_parts = [
                "## Active Skills\n\n"
                "These skills are **conversational guidance** — follow them directly in your "
                "responses. Do NOT spawn an agent or use the agent tool to handle them. "
                "They describe how YOU should approach the conversation, not tasks to delegate.",
            ]
            for skill in routed_skills:
                auto_parts.append(f"### {skill.name}\n{skill.content}")
            sections.append(PromptSection(content="\n\n".join(auto_parts), scope="global", priority=30))

        # ------------------------------------------------------------------ #
        # PROJECT scope — project index, CLAUDE.md
        # Shared across sessions in the same project; cached at project boundary.
        # ------------------------------------------------------------------ #

        # Project index (cache-safe — changes infrequently)
        if project_index:
            _KIND_PRIORITY = {"class": 0, "function": 1, "export": 2, "method": 3, "variable": 4}
            sorted_symbols = sorted(project_index.symbols, key=lambda s: _KIND_PRIORITY.get(s.kind, 99))[:100]
            lines = [f"  {s.kind} {s.name} — {s.file}:{s.line}" for s in sorted_symbols]
            sections.append(PromptSection(
                content=f"## Project Index ({len(project_index.files)} files)\n\n" + "\n".join(lines),
                scope="project",
                priority=10,
            ))

        # Project instructions from CLAUDE.md / INSTRUCTIONS.md
        if context.instructions:
            sections.append(PromptSection(
                content=f"## Project Instructions\n\n{context.instructions}",
                scope="project",
                priority=20,
            ))

        # ------------------------------------------------------------------ #
        # SESSION scope — environment, memory, active skills (per-session)
        # ------------------------------------------------------------------ #

        # MCP server instructions (injected per-server, per-session)
        if mcp_instructions:
            for server_name, instr in mcp_instructions.items():
                clean_instr, warnings = sanitize_mcp_instructions(
                    server_name, instr,
                )
                for w in warnings:
                    logger.warning(w)
                sections.append(PromptSection(
                    content=f"## MCP Server: {server_name}\n\n{clean_instr}",
                    scope="session",
                    priority=0,
                ))

        # Active command skill (one-shot, dynamic)
        if active_skill_content:
            sections.append(PromptSection(
                content=f"## Active Skill\n\n{active_skill_content}",
                scope="session",
                priority=5,
            ))

        # Environment section (dynamic — cwd, date, git status)
        env_lines = [
            "## Environment",
            f"- Working directory: {context.cwd}",
            f"- Platform: {platform.system()}",
            f"- Date: {date.today().isoformat()}",
        ]
        if context.is_git_repo and context.git_status:
            env_lines.append(f"- Git status:\n```\n{context.git_status}\n```")
        elif context.is_git_repo:
            env_lines.append("- Git status: clean")
        sections.append(PromptSection(content="\n".join(env_lines), scope="session", priority=10))

        # Memory summaries (dynamic — recent session history)
        if memory_summaries:
            sections.append(PromptSection(
                content="## Recent Sessions\n\n" + "\n".join(f"- {s[:200]}" for s in memory_summaries),
                scope="session",
                priority=20,
            ))

        # Memory entries (dynamic — project-scoped key-value memory)
        if memory_entries:
            lines = [f"- **{k}**: {v[:200]}" for k, v in memory_entries.items()]
            sections.append(PromptSection(
                content="## Project Memory\n\n" + "\n".join(lines),
                scope="session",
                priority=21,
            ))

        # Composable prompt snippets (conditional enrichment)
        try:
            from llm_code.runtime.prompt_snippets import BUILTIN_SNIPPETS, compose_system_prompt
            snippet_text = compose_system_prompt(
                BUILTIN_SNIPPETS,
                is_local=is_local_model,
                force_xml=not native_tools,
            )
            if snippet_text.strip():
                sections.append(PromptSection(
                    content=f"## Snippets\n\n{snippet_text}",
                    scope="session",
                    priority=25,
                ))
        except Exception:
            pass

        # Incomplete tasks from prior sessions (cross-session persistence)
        if task_manager is not None:
            from llm_code.task.manager import build_incomplete_tasks_prompt
            task_section = build_incomplete_tasks_prompt(task_manager)
            if task_section:
                sections.append(PromptSection(content=task_section, scope="session", priority=30))

        # Plan-mode reminder — injected when the active policy is PLAN
        # so the model can't forget the read-only constraint. Priority
        # 1 in the session scope so it lands near the top of the
        # dynamic section but after the MCP / active-skill headers.
        if permission_policy is not None:
            mode = getattr(permission_policy, "mode", None)
            if mode is not None and str(getattr(mode, "value", mode)) == "plan":
                from llm_code.runtime.prompt_mode_reminders import (
                    plan_mode_reminder,
                    plan_mode_reminder_anthropic,
                )
                lower = (model_name or "").lower()
                is_anthropic_family = (
                    "claude" in lower or "anthropic" in lower
                    or "sonnet" in lower or "opus" in lower or "haiku" in lower
                )
                if is_anthropic_family:
                    reminder = plan_mode_reminder_anthropic(plan_file=plan_file)
                else:
                    reminder = plan_mode_reminder(plan_file=plan_file)
                sections.append(PromptSection(
                    content=reminder, scope="session", priority=1,
                ))

        return self._serialize(sections)

    def _serialize(self, sections: list[PromptSection]) -> str:
        """Serialize PromptSection list into a single string with cache boundary markers.

        Sections are grouped by scope and sorted by priority within each scope.
        Cache boundary markers are inserted between scope transitions:
        - Between global and project scopes
        - Between project and session scopes

        This allows API-level caching at two boundaries instead of one.
        """
        scope_order: dict[ScopeType, int] = {"global": 0, "project": 1, "session": 2}
        sorted_sections = sorted(sections, key=lambda s: (scope_order[s.scope], s.priority))

        parts: list[str] = []
        prev_scope: ScopeType | None = None

        for section in sorted_sections:
            current_scope = section.scope
            if prev_scope is not None and current_scope != prev_scope:
                # Insert cache boundary marker between scope transitions
                parts.append(_CACHE_BOUNDARY)
                parts.append(_CACHE_CONTROL_MARKER)
            parts.append(section.content)
            prev_scope = current_scope

        return "\n\n".join(parts)
