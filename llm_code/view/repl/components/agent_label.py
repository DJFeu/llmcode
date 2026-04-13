"""Sub-agent response label with rotating color palette (M15 Task E1).

Prefixes a sub-agent's message body with ``[<agent_name>] `` in a
stable color derived from the agent name. Uses
:attr:`BrandPalette.agent_palette` — six distinct tones — and
hashes the agent name to pick one deterministically, so the same
agent always gets the same color within a session.
"""
from __future__ import annotations

from rich.text import Text

from llm_code.view.repl import style

__all__ = ["render_agent_label", "color_for_agent"]


def color_for_agent(agent_name: str) -> str:
    """Return a stable color for ``agent_name`` from the rotating palette."""
    palette = style.palette.agent_palette
    idx = hash(agent_name) % len(palette)
    return palette[idx]


def render_agent_label(agent_name: str, body: str) -> Text:
    out = Text()
    color = color_for_agent(agent_name)
    out.append(f"[{agent_name}] ", style=f"bold {color}")
    out.append(body, style=style.palette.assistant_fg)
    return out
