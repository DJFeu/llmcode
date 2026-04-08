"""Dynamic prompt section builder.

Generates a markdown "Active Capabilities" block from the live tool list and
routed skills available this turn, so the model gets a delegation hint that
matches what it actually has access to.

Pure functions only — no I/O, no global state, no logging. Safe to call from
inside SystemPromptBuilder.build().
"""
from __future__ import annotations

from collections import OrderedDict

from llm_code.api.types import ToolDefinition
from llm_code.runtime.skills import Skill

# Canonical tool buckets (order matters — used for stable rendering).
TOOL_CATEGORIES: tuple[str, ...] = (
    "read",
    "search",
    "write",
    "exec",
    "lsp",
    "web",
    "agent",
    "other",
)

# Lower-cased prefix / keyword -> category. First match wins; checked in order.
_TOOL_RULES: tuple[tuple[str, str], ...] = (
    # LSP (check before read/write/etc to avoid "lsp_..." matching other rules)
    ("lsp_", "lsp"),
    ("lsp", "lsp"),
    # Web (check before "fetch"/"search" generic rules)
    ("web_", "web"),
    ("webfetch", "web"),
    ("websearch", "web"),
    ("web", "web"),
    # Search
    ("grep", "search"),
    ("glob", "search"),
    ("ripgrep", "search"),
    ("find", "search"),
    # Read
    ("read_file", "read"),
    ("readfile", "read"),
    ("notebook_read", "read"),
    ("read", "read"),
    # Agent / task management (check before write rules so "task_create" doesn't hit "create")
    ("task_", "agent"),
    ("subagent", "agent"),
    ("delegate", "agent"),
    ("agent", "agent"),
    # Write
    ("write_file", "write"),
    ("writefile", "write"),
    ("edit_file", "write"),
    ("editfile", "write"),
    ("multi_edit", "write"),
    ("multiedit", "write"),
    ("notebook_edit", "write"),
    ("write", "write"),
    ("edit", "write"),
    ("create", "write"),
    # Exec
    ("bash", "exec"),
    ("shell", "exec"),
    ("execute", "exec"),
    ("run_", "exec"),
    # Web fetch (late-bound fallback)
    ("fetch", "web"),
)

# Per-section truncation knobs.
DEFAULT_MAX_TOOLS = 24
DEFAULT_MAX_SKILLS = 12
_TOOL_DESC_CHARS = 80
_SKILL_DESC_CHARS = 100
_TRIGGER_CHARS = 80


def classify_tool(tool_name: str) -> str:
    """Return the canonical category for *tool_name*.

    Matching is case-insensitive. Falls back to ``"other"`` if no rule matches.
    """
    lname = tool_name.lower()
    for needle, category in _TOOL_RULES:
        if needle in lname:
            return category
    return "other"


def group_skills_by_category(skills: tuple[Skill, ...]) -> dict[str, list[Skill]]:
    """Group *skills* by their first tag (or ``"general"`` if untagged)."""
    grouped: "OrderedDict[str, list[Skill]]" = OrderedDict()
    for skill in skills:
        category = skill.tags[0] if skill.tags else "general"
        grouped.setdefault(category, []).append(skill)
    return grouped


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip().replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _render_tool_table(tools: tuple[ToolDefinition, ...], max_tools: int) -> list[str]:
    if not tools:
        return []
    shown = tools[:max_tools]
    overflow = len(tools) - len(shown)

    by_category: "OrderedDict[str, list[ToolDefinition]]" = OrderedDict()
    for tool in shown:
        by_category.setdefault(classify_tool(tool.name), []).append(tool)

    lines: list[str] = ["### Tools by Capability"]
    for category, bucket in by_category.items():
        lines.append(f"- **{category}**:")
        for tool in bucket:
            desc = _truncate(tool.description, _TOOL_DESC_CHARS)
            lines.append(f"    - `{tool.name}` — {desc}")
    if overflow > 0:
        lines.append(f"- _(+{overflow} more)_")
    return lines


def _render_key_triggers(skills: tuple[Skill, ...], max_skills: int) -> list[str]:
    if not skills:
        return []
    shown = skills[:max_skills]
    overflow = len(skills) - len(shown)
    lines: list[str] = [
        "### Key Triggers",
        "_When the user's request matches one of these triggers, lean on the "
        "corresponding skill instead of improvising:_",
    ]
    for skill in shown:
        trigger = _truncate(skill.trigger or skill.name, _TRIGGER_CHARS)
        desc = _truncate(skill.description, _SKILL_DESC_CHARS)
        lines.append(f"- **{skill.name}** — _trigger:_ {trigger}  \n    {desc}")
    if overflow > 0:
        lines.append(f"- _(+{overflow} more)_")
    return lines


def _render_skill_categories(skills: tuple[Skill, ...], max_skills: int) -> list[str]:
    if not skills:
        return []
    shown = skills[:max_skills]
    overflow = len(skills) - len(shown)
    grouped = group_skills_by_category(tuple(shown))
    lines: list[str] = ["### Skills by Category"]
    for category, members in grouped.items():
        lines.append(f"- **{category}**:")
        for skill in members:
            desc = _truncate(skill.description, _SKILL_DESC_CHARS)
            lines.append(f"    - `{skill.name}` — {desc}")
    if overflow > 0:
        lines.append(f"- _(+{overflow} more)_")
    return lines


def build_delegation_section(
    tools: tuple[ToolDefinition, ...],
    skills: tuple[Skill, ...],
    *,
    max_tools: int = DEFAULT_MAX_TOOLS,
    max_skills: int = DEFAULT_MAX_SKILLS,
) -> str:
    """Return a markdown ``## Active Capabilities`` section, or empty string."""
    if not tools and not skills:
        return ""

    parts: list[str] = ["## Active Capabilities"]
    parts.append(
        "_The following tools and skills are available this turn. Prefer the most "
        "specific capability for each task; do not invent new ones._"
    )

    tool_lines = _render_tool_table(tools, max_tools)
    if tool_lines:
        parts.append("\n".join(tool_lines))

    trigger_lines = _render_key_triggers(skills, max_skills)
    if trigger_lines:
        parts.append("\n".join(trigger_lines))

    category_lines = _render_skill_categories(skills, max_skills)
    if category_lines:
        parts.append("\n".join(category_lines))

    return "\n\n".join(parts)
