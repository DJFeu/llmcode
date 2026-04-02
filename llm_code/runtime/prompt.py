"""System prompt builder for the conversation runtime."""
from __future__ import annotations

import json
import platform
from datetime import date
from typing import TYPE_CHECKING

from llm_code.api.types import ToolDefinition
from llm_code.runtime.context import ProjectContext

if TYPE_CHECKING:
    from llm_code.runtime.skills import SkillSet

_INTRO = """\
You are a coding assistant running inside a terminal. \
You have access to tools that let you read, write, and edit files, \
search code, and run shell commands. \
Think step-by-step before taking any action.\
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
    ) -> str:
        # ------------------------------------------------------------------ #
        # STATIC / CACHE-SAFE section (above the cache boundary)
        # ------------------------------------------------------------------ #
        static_parts: list[str] = [_INTRO]

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

        # ------------------------------------------------------------------ #
        # DYNAMIC section (below the cache boundary)
        # ------------------------------------------------------------------ #
        dynamic_parts: list[str] = [_CACHE_BOUNDARY]

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

        # Combine: static parts joined by \n\n, then dynamic parts joined by \n\n
        all_parts = static_parts + dynamic_parts
        return "\n\n".join(all_parts)
