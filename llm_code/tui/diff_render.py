"""Structured unified-diff renderer for ToolBlock display.

Inspired by Claude Code's StructuredDiff component. Renders a unified diff
with cyan hunk headers, green/red colored add/remove lines, dim context lines,
left-gutter line numbers, and a truncation footer.
"""
from __future__ import annotations

import difflib
import re
from typing import Iterable

from rich.text import Text

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

# Style palette (kept consistent with chat_widgets ToolBlock colors)
_STYLE_HUNK = "bold cyan"
_STYLE_ADD = "green on #0a2e0a"
_STYLE_DEL = "red on #2e0a0a"
_STYLE_CTX = "dim"
_STYLE_GUTTER = "dim cyan"
_STYLE_FOOTER = "dim italic"


def _unified_diff_lines(old: str, new: str, file_path: str) -> list[str]:
    """Generate unified-diff lines for two strings."""
    old_lines = old.splitlines(keepends=False)
    new_lines = new.splitlines(keepends=False)
    label = file_path or "file"
    return list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{label}",
            tofile=f"b/{label}",
            lineterm="",
            n=3,
        )
    )


def render_diff(
    old: str,
    new: str,
    file_path: str = "",
    max_lines: int = 40,
) -> Text:
    """Render a unified diff between *old* and *new* as a rich Text.

    Lines are color-coded with hunk headers in cyan, additions on dark green,
    removals on dark red, and context dim. Output is truncated to *max_lines*
    body lines (header lines excluded) with a footer indicating remaining
    lines if truncation occurred.
    """
    raw = _unified_diff_lines(old, new, file_path)
    return render_diff_lines(raw, max_lines=max_lines)


def render_diff_lines(diff_lines: Iterable[str], max_lines: int = 40) -> Text:
    """Render an existing unified-diff line iterable.

    This is the entry point used by ToolBlock when the runtime already
    produced diff lines (edit_file/write_file emit them directly).
    """
    text = Text()
    body_count = 0
    truncated_remaining = 0
    old_ln = 0
    new_ln = 0
    saw_hunk = False
    rendered_any = False

    lines = list(diff_lines)
    # Skip the leading file header lines (--- / +++) if present.
    body: list[str] = []
    for line in lines:
        if line.startswith("---") or line.startswith("+++"):
            continue
        body.append(line)

    for line in body:
        if line.startswith("@@"):
            saw_hunk = True
            m = _HUNK_RE.match(line)
            if m:
                old_ln = int(m.group(1))
                new_ln = int(m.group(3))
            if rendered_any:
                text.append("\n")
            text.append(line, style=_STYLE_HUNK)
            rendered_any = True
            continue

        if body_count >= max_lines:
            truncated_remaining += 1
            continue

        if rendered_any:
            text.append("\n")
        rendered_any = True
        body_count += 1

        if line.startswith("+") and not line.startswith("+++"):
            gutter = f"{new_ln:>4} "
            text.append(gutter, style=_STYLE_GUTTER)
            text.append(line, style=_STYLE_ADD)
            new_ln += 1
        elif line.startswith("-") and not line.startswith("---"):
            gutter = f"{old_ln:>4} "
            text.append(gutter, style=_STYLE_GUTTER)
            text.append(line, style=_STYLE_DEL)
            old_ln += 1
        else:
            # context line
            gutter = f"{new_ln:>4} " if saw_hunk else "     "
            text.append(gutter, style=_STYLE_GUTTER)
            text.append(line, style=_STYLE_CTX)
            old_ln += 1
            new_ln += 1

    if truncated_remaining:
        if rendered_any:
            text.append("\n")
        text.append(
            f"     … +{truncated_remaining} more line"
            f"{'s' if truncated_remaining != 1 else ''}",
            style=_STYLE_FOOTER,
        )

    return text
