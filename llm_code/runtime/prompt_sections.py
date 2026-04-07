"""Dynamic prompt section builders.

Currently exposes only the personas section builder. Tools and skills are
rendered elsewhere in the prompt pipeline (the tool registry is serialized
directly by the provider adapter, and skill descriptions are injected by
``SystemPromptBuilder`` / ``SkillRouter``), so dedicated helpers for those
sections are intentionally absent here.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any, Mapping


def build_personas_section(personas: Mapping[str, Any]) -> str:
    """Render the available personas as a system-prompt section."""
    if not personas:
        return ""
    lines = ["## Available Personas"]
    for name, persona in personas.items():
        desc = getattr(persona, "description", "") or ""
        lines.append(f"- **{name}**: {desc}".rstrip())
    return "\n".join(lines)


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
