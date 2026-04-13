"""Inline token warning (M15 Task F3).

Renders a single-line warning when context-window fill crosses 80%.
"""
from __future__ import annotations

from rich.text import Text

from llm_code.view.repl import style

__all__ = ["render_token_warning", "should_warn"]

_WARN_THRESHOLD = 0.80


def should_warn(used: int, limit: int) -> bool:
    if limit <= 0:
        return False
    return used / limit >= _WARN_THRESHOLD


def render_token_warning(used: int, limit: int) -> Text:
    pct = int(round(100 * used / max(1, limit)))
    out = Text()
    out.append(
        f"{style.ICON_WARNING} ",
        style=f"bold {style.palette.status_warning}",
    )
    out.append(
        f"context {pct}% full — consider /compact to reduce history",
        style=style.palette.status_warning,
    )
    return out
