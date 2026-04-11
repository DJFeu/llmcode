"""Bright-white assistant text renderer with bullet prefix (M15 Task C1)."""
from __future__ import annotations

from rich.text import Text

from llm_code.view.repl import style

__all__ = ["render_assistant_text"]


def render_assistant_text(body: str) -> Text:
    """Return a Rich ``Text`` with the leading ● bullet + bright white body.

    First line gets a ``●`` glyph in ``palette.assistant_bullet`` tone;
    all lines render in ``palette.assistant_fg`` (bright white).
    """
    out = Text()
    out.append("● ", style=f"bold {style.palette.assistant_bullet}")
    out.append(body, style=style.palette.assistant_fg)
    return out
