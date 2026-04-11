"""Compact summary panel renderer (M15 Task C5)."""
from __future__ import annotations

from rich.panel import Panel
from rich.text import Text

from llm_code.view.repl import style

__all__ = ["render_compact_summary"]


def render_compact_summary(
    *, before_tokens: int, after_tokens: int, tokens_saved: int
) -> Panel:
    saved_pct = 0
    if before_tokens > 0:
        saved_pct = int(round(100 * tokens_saved / before_tokens))
    body = Text()
    body.append(f"Before: ", style=style.palette.system_fg)
    body.append(f"{before_tokens} tokens\n", style="bold")
    body.append(f"After:  ", style=style.palette.system_fg)
    body.append(f"{after_tokens} tokens\n", style="bold")
    body.append(f"Saved:  ", style=style.palette.system_fg)
    body.append(
        f"{tokens_saved} tokens ({saved_pct}%)",
        style=f"bold {style.palette.status_success}",
    )
    return Panel(
        body,
        title=f"[bold {style.palette.brand_accent}]/compact summary[/]",
        border_style=style.palette.brand_accent,
        padding=(0, 2),
    )
