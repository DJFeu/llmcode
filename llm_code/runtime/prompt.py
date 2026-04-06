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
from llm_code.runtime.prompt_guard import sanitize_mcp_instructions

if TYPE_CHECKING:
    from llm_code.runtime.indexer import ProjectIndex
    from llm_code.runtime.memory_layers import GovernanceRule
    from llm_code.runtime.skills import SkillSet
    from llm_code.task.manager import TaskLifecycleManager

logger = logging.getLogger(__name__)

_INTRO = """\
You are a coding assistant running inside a terminal. \
You have access to tools that let you read, write, and edit files, \
search code, and run shell commands. \
Do NOT output your thinking process or reasoning steps. \
Go directly to your answer or tool call.\
"""

_BEHAVIOR_RULES = """\
Rules:
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
    ) -> str:
        sections: list[PromptSection] = []

        # ------------------------------------------------------------------ #
        # GLOBAL scope — governance rules, behavior rules, tool instructions
        # Shared across all projects; cached at global boundary.
        # ------------------------------------------------------------------ #
        sections.append(PromptSection(content=_INTRO, scope="global", priority=0))

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

        if not native_tools and tools:
            sections.append(PromptSection(content=_XML_TOOL_INSTRUCTIONS, scope="global", priority=20))
            tool_lines = ["Available tools:"]
            for t in tools:
                schema_str = json.dumps(t.input_schema, separators=(",", ":"))
                tool_lines.append(f"  - {t.name}: {t.description}  schema={schema_str}")
            sections.append(PromptSection(content="\n".join(tool_lines), scope="global", priority=21))

        # Auto skills are relatively stable and treated as global
        if skills and skills.auto_skills:
            auto_parts = ["## Active Skills"]
            for skill in skills.auto_skills:
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

        # Incomplete tasks from prior sessions (cross-session persistence)
        if task_manager is not None:
            from llm_code.task.manager import build_incomplete_tasks_prompt
            task_section = build_incomplete_tasks_prompt(task_manager)
            if task_section:
                sections.append(PromptSection(content=task_section, scope="session", priority=30))

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
