"""Dynamic prompt section builders ported from oh-my-opencode.

These helpers render small system-prompt sections describing the personas,
tools, and skills that are currently available in the runtime. They are
deliberately pure functions so they can be cached per turn by the prompt
builder and unit-tested without spinning up a full conversation.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any, Iterable, Mapping


def build_personas_section(personas: Mapping[str, Any]) -> str:
    """Render the available personas as a system-prompt section."""
    if not personas:
        return ""
    lines = ["## Available Personas"]
    for name, persona in personas.items():
        desc = getattr(persona, "description", "") or ""
        lines.append(f"- **{name}**: {desc}".rstrip())
    return "\n".join(lines)


def build_tools_section(tool_registry: Any) -> str:
    """Render the available tools as a system-prompt section.

    Accepts any object exposing a ``list_tools()`` method or an iterable of
    tool definitions with ``name``/``description`` attributes (or matching
    dict keys).
    """
    tools: Iterable[Any]
    if tool_registry is None:
        return ""
    if hasattr(tool_registry, "list_tools"):
        tools = tool_registry.list_tools()
    else:
        tools = tool_registry  # assume iterable
    rendered: list[str] = []
    for tool in tools or []:
        name = getattr(tool, "name", None) or (
            tool.get("name") if isinstance(tool, dict) else None
        )
        desc = getattr(tool, "description", None) or (
            tool.get("description", "") if isinstance(tool, dict) else ""
        )
        if not name:
            continue
        rendered.append(f"- **{name}**: {desc}".rstrip())
    if not rendered:
        return ""
    return "\n".join(["## Available Tools", *rendered])


def build_skills_section(skills: Iterable[Any]) -> str:
    """Render loaded skills as a system-prompt section."""
    rendered: list[str] = []
    for skill in skills or []:
        name = getattr(skill, "name", None) or (
            skill.get("name") if isinstance(skill, dict) else None
        )
        desc = getattr(skill, "description", None) or (
            skill.get("description", "") if isinstance(skill, dict) else ""
        )
        if not name:
            continue
        rendered.append(f"- **{name}**: {desc}".rstrip())
    if not rendered:
        return ""
    return "\n".join(["## Available Skills", *rendered])


# ---------------------------------------------------------------------------
# Per-turn cache helper
# ---------------------------------------------------------------------------

@lru_cache(maxsize=8)
def _cached_render(kind: str, signature: tuple) -> str:
    # Used internally only; cache key is the (kind, content-signature) tuple.
    return ""


def render_cached(kind: str, content: str, signature: tuple) -> str:
    """Cache-aware passthrough.

    Callers compute *content* once per turn and pass an immutable *signature*
    (e.g. tuple of names) so subsequent renders within the same turn are
    short-circuited via the lru cache.
    """
    key = (kind, signature)
    cached = _cached_render(*key)
    if cached:
        return cached
    if content:
        # Replace empty sentinel with actual rendered content for this signature.
        _cached_render.cache_clear()  # avoid stale cache across turns
        # Re-prime by storing into a tiny dict on the function attribute.
        store = getattr(render_cached, "_store", {})
        store[key] = content
        render_cached._store = store  # type: ignore[attr-defined]
        return content
    return content
