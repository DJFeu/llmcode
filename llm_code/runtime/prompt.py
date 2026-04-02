"""System prompt builder for the conversation runtime."""
from __future__ import annotations

import json
import platform
from datetime import date

from llm_code.api.types import ToolDefinition
from llm_code.runtime.context import ProjectContext

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


class SystemPromptBuilder:
    def build(
        self,
        context: ProjectContext,
        tools: tuple[ToolDefinition, ...] = (),
        native_tools: bool = True,
    ) -> str:
        parts: list[str] = [_INTRO]

        # XML tool-calling instructions (only when provider does not support native tools)
        if not native_tools and tools:
            parts.append(_XML_TOOL_INSTRUCTIONS)
            tool_lines = ["Available tools:"]
            for t in tools:
                schema_str = json.dumps(t.input_schema, separators=(",", ":"))
                tool_lines.append(f"  - {t.name}: {t.description}  schema={schema_str}")
            parts.append("\n".join(tool_lines))

        # Project instructions
        if context.instructions:
            parts.append(f"## Project Instructions\n\n{context.instructions}")

        # Environment section
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

        parts.append("\n".join(env_lines))

        return "\n\n".join(parts)
