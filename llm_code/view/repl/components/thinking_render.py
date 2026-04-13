"""Thinking text block renderer (M15 Task C3).

Renders the assistant's internal thinking trace as a dim block
with a colored header. When the block is longer than the preview
threshold, it emits a ``Ctrl+O to expand`` marker — the truncation
registry picks it up and the expand keybinding can dump the full
body.
"""
from __future__ import annotations

from rich.console import Group, RenderableType
from rich.text import Text

from llm_code.view.repl import style

__all__ = ["render_thinking", "PREVIEW_LINES"]

PREVIEW_LINES = 6


def render_thinking(
    body: str,
    *,
    tokens: int = 0,
    elapsed: float = 0.0,
    collapsed: bool = True,
) -> RenderableType:
    """Return a Rich renderable for the thinking block.

    Parameters
    ----------
    body:
        Full thinking text.
    tokens:
        Token count for the header label.
    elapsed:
        Elapsed seconds for the header label.
    collapsed:
        When True, only the first :data:`PREVIEW_LINES` lines are
        shown with a trailing ``Ctrl+O to expand`` marker. When
        False, the full body is rendered.
    """
    header = Text(
        f"[thinking: {tokens} tokens, {elapsed:.1f}s]",
        style=f"bold {style.palette.thinking_header_fg}",
    )
    lines = body.splitlines() or [""]
    if collapsed and len(lines) > PREVIEW_LINES:
        preview = lines[:PREVIEW_LINES]
        remaining = len(lines) - PREVIEW_LINES
        body_text = Text("\n".join(preview), style=style.palette.thinking_fg)
        marker = Text(
            f"\n[… {remaining} more lines · Ctrl+O to expand]",
            style=style.palette.hint_fg,
        )
        body_text.append(marker)
    else:
        body_text = Text("\n".join(lines), style=style.palette.thinking_fg)
    return Group(header, body_text)
