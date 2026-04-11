"""Structured unified-diff renderer with per-line colors + line numbers (D2).

Parses a unified diff and renders:

- ``---`` / ``+++`` file headers in dim
- ``@@ -a,b +c,d @@`` hunk headers in cyan
- ``-`` lines with a red background (``palette.diff_del_*``)
- ``+`` lines with a green background (``palette.diff_add_*``)
- context lines in the default tone
- optional left gutter line numbers
"""
from __future__ import annotations

from typing import Iterable, List

from rich.console import Group, RenderableType
from rich.text import Text

from llm_code.view.repl import style

__all__ = ["render_structured_diff"]


def render_structured_diff(
    diff_text: str, *, filename: str | None = None
) -> RenderableType:
    lines = diff_text.splitlines()
    out: List[RenderableType] = []
    old_lineno = 0
    new_lineno = 0
    for line in lines:
        if line.startswith("---") or line.startswith("+++"):
            out.append(Text(line, style=style.palette.diff_lineno_fg))
        elif line.startswith("@@"):
            out.append(Text(line, style=f"bold {style.palette.diff_hunk_fg}"))
            old_lineno, new_lineno = _parse_hunk_header(line)
        elif line.startswith("-") and not line.startswith("---"):
            gutter = f"{old_lineno:>4}      │ "
            t = Text()
            t.append(gutter, style=style.palette.diff_lineno_fg)
            t.append(
                line,
                style=f"{style.palette.diff_del_fg} on {style.palette.diff_del_bg}",
            )
            out.append(t)
            old_lineno += 1
        elif line.startswith("+") and not line.startswith("+++"):
            gutter = f"     {new_lineno:>4} │ "
            t = Text()
            t.append(gutter, style=style.palette.diff_lineno_fg)
            t.append(
                line,
                style=f"{style.palette.diff_add_fg} on {style.palette.diff_add_bg}",
            )
            out.append(t)
            new_lineno += 1
        else:
            gutter = f"{old_lineno:>4} {new_lineno:>4} │ "
            t = Text()
            t.append(gutter, style=style.palette.diff_lineno_fg)
            t.append(line, style=style.palette.system_fg)
            out.append(t)
            old_lineno += 1
            new_lineno += 1
    return Group(*out)


def _parse_hunk_header(header: str) -> tuple[int, int]:
    """Parse ``@@ -old,len +new,len @@`` and return (old_start, new_start)."""
    try:
        # Split "@@ -10,3 +12,5 @@" → ["@@", "-10,3", "+12,5", "@@", ...]
        parts = header.split()
        old_part = next(p for p in parts if p.startswith("-"))
        new_part = next(p for p in parts if p.startswith("+"))
        old_start = int(old_part[1:].split(",")[0])
        new_start = int(new_part[1:].split(",")[0])
        return old_start, new_start
    except Exception:
        return 1, 1
