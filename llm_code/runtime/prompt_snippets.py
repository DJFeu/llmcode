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
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PromptSnippet:
    """A conditional segment of the system prompt.

    ``tags`` (v2.6.1 M2) is a tuple of semantic identifiers describing
    *what* this snippet provides. When a model template declares
    ``provides_tags`` (via the sidecar ``<template>.metadata.toml``)
    and the active profile opts in to ``prompt_dedupe_with_template``,
    ``compose_system_prompt`` skips any snippet whose ``tags`` are a
    subset of the template's ``provides_tags`` — eliminating
    duplicate guidance the model has already been told once.

    Snippets without tags (legacy default) never participate in the
    dedupe path, preserving v2.6.0 byte-parity.
    """
    key: str
    content: str
    condition: Callable[..., bool] | None = None
    priority: int = 50
    tags: tuple[str, ...] = ()


def compose_system_prompt(
    snippets: list[PromptSnippet],
    *,
    provides_tags: tuple[str, ...] = (),
    **context: Any,
) -> str:
    """Evaluate conditions, deduplicate, sort by priority, and join.

    Parameters
    ----------
    snippets:
        All registered snippets (may include disabled ones).
    provides_tags:
        Semantic tags the active model template already supplies. Any
        snippet whose ``tags`` are a non-empty subset of
        ``provides_tags`` is dropped — the template already conveys
        the same guidance and re-rendering would burn tokens. Default
        ``()`` preserves v2.6.0 behavior (every snippet renders).
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

    provided = set(provides_tags)

    # Evaluate conditions and filter
    active: list[PromptSnippet] = []
    for s in by_key.values():
        # v2.6.1 M2 — drop snippets whose tags are fully covered by
        # the active template. ``s.tags`` is non-empty AND
        # ``set(s.tags) <= provided`` ⇒ the template already says
        # everything this snippet would say.
        if s.tags and provided and set(s.tags).issubset(provided):
            logger.debug(
                "Prompt snippet '%s' dropped — template provides %s",
                s.key, sorted(s.tags),
            )
            continue
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
    tags=("intro",),
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
    tags=("behavior_rules",),
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
    tags=("local_model_rules",),
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
    tags=("xml_tools",),
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
    tags=("tool_result_nudge",),
)

# Collect all built-in snippets
BUILTIN_SNIPPETS: list[PromptSnippet] = [
    INTRO,
    BEHAVIOR_RULES,
    LOCAL_MODEL_RULES,
    XML_TOOL_INSTRUCTIONS,
    TOOL_RESULT_NUDGE,
]
