"""Design-system primitives — Divider, StatusIcon, etc. (M15 Task F4).

Thin Rich wrappers used across Groups C/D/E. Keeps the dependency
graph shallow: higher-level components import from here instead
of recreating the same patterns.
"""
from __future__ import annotations

from typing import Literal

from rich.rule import Rule
from rich.text import Text

from llm_code.view.repl import style

__all__ = [
    "divider",
    "status_icon",
    "keyboard_hint",
    "loading_state",
    "progress_bar",
]


def divider(char: str = "─", color: str | None = None) -> Rule:
    """Horizontal rule using a brand-accent-colored character."""
    return Rule(
        characters=char,
        style=color or style.palette.brand_muted,
    )


IconKind = Literal["success", "failure", "warning", "info", "start", "dot"]


def status_icon(kind: IconKind) -> Text:
    glyph, tone = {
        "success": (style.ICON_SUCCESS, style.palette.status_success),
        "failure": (style.ICON_FAILURE, style.palette.status_error),
        "warning": (style.ICON_WARNING, style.palette.status_warning),
        "info": (style.ICON_INFO, style.palette.status_info),
        "start": (style.ICON_START, style.palette.tool_start_fg),
        "dot": (style.ICON_DOT, style.palette.hint_fg),
    }.get(kind, (style.ICON_DOT, style.palette.hint_fg))
    return Text(glyph, style=f"bold {tone}")


def keyboard_hint(keys: str, action: str) -> Text:
    out = Text()
    out.append(keys, style=f"bold {style.palette.brand_accent}")
    out.append(f" {action}", style=style.palette.hint_fg)
    return out


def loading_state(text: str) -> Text:
    return Text(f"… {text}", style=style.palette.hint_fg)


def progress_bar(ratio: float, width: int = 20) -> Text:
    ratio = max(0.0, min(1.0, ratio))
    filled = int(round(ratio * width))
    empty = width - filled
    bar = "█" * filled + "░" * empty
    return Text(bar, style=style.palette.brand_accent)
