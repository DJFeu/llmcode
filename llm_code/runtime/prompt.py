"""System prompt builder for the conversation runtime."""
from __future__ import annotations

import json
import platform
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from llm_code.api.types import ToolDefinition
from llm_code.runtime.context import ProjectContext

if TYPE_CHECKING:
    from llm_code.runtime.indexer import ProjectIndex
    from llm_code.runtime.memory_layers import GovernanceRule
    from llm_code.runtime.skills import SkillSet
    from llm_code.task.manager import TaskLifecycleManager

_INTRO = """\
You are a coding assistant running inside a terminal. \
You have access to tools that let you read, write, and edit files, \
search code, and run shell commands. \
Think step-by-step before taking any action.\
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
        # ------------------------------------------------------------------ #
        # STATIC / CACHE-SAFE section (above the cache boundary)
        # ------------------------------------------------------------------ #
        static_parts: list[str] = [_INTRO]

        # Governance rules (L0) — injected at the very start, before behavior rules
        if governance_rules:
            gov_lines = ["## Governance Rules\n"]
            # Group by category
            categories: dict[str, list] = {}
            for rule in governance_rules:
                categories.setdefault(rule.category, []).append(rule)
            for cat, cat_rules in categories.items():
                gov_lines.append(f"### {cat.title()}")
                for r in cat_rules:
                    source_name = Path(r.source).name if r.source else "unknown"
                    gov_lines.append(f"- {r.content} _(from {source_name})_")
                gov_lines.append("")
            static_parts.append("\n".join(gov_lines))

        static_parts.append(_BEHAVIOR_RULES)

        # XML tool-calling instructions (only when provider does not support native tools)
        if not native_tools and tools:
            static_parts.append(_XML_TOOL_INSTRUCTIONS)
            tool_lines = ["Available tools:"]
            for t in tools:
                schema_str = json.dumps(t.input_schema, separators=(",", ":"))
                tool_lines.append(f"  - {t.name}: {t.description}  schema={schema_str}")
            static_parts.append("\n".join(tool_lines))

        # Auto skills (cache-safe — they change rarely)
        if skills and skills.auto_skills:
            auto_parts = ["## Active Skills"]
            for skill in skills.auto_skills:
                auto_parts.append(f"### {skill.name}\n{skill.content}")
            static_parts.append("\n\n".join(auto_parts))

        # Project index (cache-safe — changes infrequently)
        if project_index:
            _KIND_PRIORITY = {"class": 0, "function": 1, "export": 2, "method": 3, "variable": 4}
            sorted_symbols = sorted(project_index.symbols, key=lambda s: _KIND_PRIORITY.get(s.kind, 99))[:100]
            lines = [f"  {s.kind} {s.name} — {s.file}:{s.line}" for s in sorted_symbols]
            static_parts.append(f"## Project Index ({len(project_index.files)} files)\n\n" + "\n".join(lines))

        # ------------------------------------------------------------------ #
        # DYNAMIC section (below the cache boundary)
        # ------------------------------------------------------------------ #
        dynamic_parts: list[str] = [_CACHE_BOUNDARY]

        # MCP server instructions (injected per-server)
        if mcp_instructions:
            for server_name, instr in mcp_instructions.items():
                dynamic_parts.append(f"## MCP Server: {server_name}\n\n{instr}")

        # Project instructions (semi-dynamic — changes per project)
        if context.instructions:
            dynamic_parts.append(f"## Project Instructions\n\n{context.instructions}")

        # Active command skill (one-shot, dynamic)
        if active_skill_content:
            dynamic_parts.append(f"## Active Skill\n\n{active_skill_content}")

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

        dynamic_parts.append("\n".join(env_lines))

        # Memory summaries (dynamic — recent session history)
        if memory_summaries:
            dynamic_parts.append("## Recent Sessions\n\n" + "\n".join(f"- {s[:200]}" for s in memory_summaries))

        # Memory entries (dynamic — project-scoped key-value memory)
        if memory_entries:
            lines = [f"- **{k}**: {v[:200]}" for k, v in memory_entries.items()]
            dynamic_parts.append("## Project Memory\n\n" + "\n".join(lines))

        # Incomplete tasks from prior sessions (cross-session persistence)
        if task_manager is not None:
            from llm_code.task.manager import build_incomplete_tasks_prompt
            task_section = build_incomplete_tasks_prompt(task_manager)
            if task_section:
                dynamic_parts.append(task_section)

        # Combine: static parts joined by \n\n, then dynamic parts joined by \n\n
        all_parts = static_parts + dynamic_parts
        return "\n\n".join(all_parts)
