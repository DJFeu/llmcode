"""Composable system prompt via conditional snippets.

Borrowed from Gemini CLI's ``snippets.ts`` pattern.

Instead of a monolithic string builder, the system prompt is assembled
from modular ``PromptSnippet`` objects.  Each snippet has:
    - ``key``: unique identifier
    - ``content``: the text to include
    - ``condition``: optional callable → include only if truthy
    - ``priority``: lower = earlier in prompt (default 50)

Snippets are evaluated, sorted, and joined.  Empty/disabled snippets
are skipped.  This allows features to register their own prompt
sections without editing the central builder.

Risk mitigations:
    - Missing snippets are silently skipped (no crash)
    - Duplicate keys: last one wins (logged as warning)
    - ``compose_system_prompt()`` is a pure function
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PromptSnippet:
    """A conditional segment of the system prompt."""
    key: str
    content: str
    condition: Callable[..., bool] | None = None
    priority: int = 50


def compose_system_prompt(
    snippets: list[PromptSnippet],
    **context: Any,
) -> str:
    """Evaluate conditions, deduplicate, sort by priority, and join.

    Parameters
    ----------
    snippets:
        All registered snippets (may include disabled ones).
    **context:
        Keyword arguments passed to each snippet's ``condition()``.

    Returns
    -------
    str
        The assembled system prompt.
    """
    # Deduplicate by key (last wins, with warning)
    by_key: dict[str, PromptSnippet] = {}
    for s in snippets:
        if s.key in by_key:
            logger.debug("Prompt snippet '%s' overridden", s.key)
        by_key[s.key] = s

    # Evaluate conditions and filter
    active: list[PromptSnippet] = []
    for s in by_key.values():
        if s.condition is not None:
            try:
                if not s.condition(**context):
                    continue
            except Exception:
                logger.debug("Snippet '%s' condition raised; skipping", s.key)
                continue
        if s.content.strip():
            active.append(s)

    # Sort by priority (stable: insertion order breaks ties)
    active.sort(key=lambda s: s.priority)

    return "\n\n".join(s.content for s in active)


# ---------------------------------------------------------------------------
# Built-in snippet definitions
# ---------------------------------------------------------------------------

INTRO = PromptSnippet(
    key="intro",
    content=(
        "You are a coding assistant running inside a terminal. "
        "You have access to tools that let you read, write, and edit files, "
        "search code, and run shell commands."
    ),
    priority=10,
)

BEHAVIOR_RULES = PromptSnippet(
    key="behavior_rules",
    content="""\
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
- For code changes, show the minimal diff needed""",
    priority=20,
)

LOCAL_MODEL_RULES = PromptSnippet(
    key="local_model_rules",
    content=(
        "- Do NOT use the agent tool unless the user explicitly asks for it or the task genuinely "
        "requires parallel sub-tasks. For normal questions, conversations, or simple tasks, "
        "respond directly without spawning agents."
    ),
    condition=lambda is_local=False, **_: is_local,
    priority=25,
)

XML_TOOL_INSTRUCTIONS = PromptSnippet(
    key="xml_tool_instructions",
    content="""\
When you need to use a tool, emit exactly one JSON block wrapped in \
<tool_call>...</tool_call> XML tags — nothing else on those lines. \
The JSON must have two keys: "tool" (the tool name) and "args" (an object \
of parameters). Example:
<tool_call>{"tool": "read_file", "args": {"path": "/README.md"}}</tool_call>
Wait for the tool result before continuing.""",
    condition=lambda force_xml=False, **_: force_xml,
    priority=30,
)

TOOL_RESULT_NUDGE = PromptSnippet(
    key="tool_result_nudge",
    content=(
        "IMPORTANT: After receiving tool results, you MUST produce a substantive "
        "response based on those results. Do NOT end your turn with only a few "
        "tokens or an empty response."
    ),
    condition=lambda is_local=False, **_: is_local,
    priority=35,
)

# Collect all built-in snippets
BUILTIN_SNIPPETS: list[PromptSnippet] = [
    INTRO,
    BEHAVIOR_RULES,
    LOCAL_MODEL_RULES,
    XML_TOOL_INSTRUCTIONS,
    TOOL_RESULT_NUDGE,
]
