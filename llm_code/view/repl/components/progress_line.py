"""Colored tool-event progress line (M15 Task D1).

Produces single-line status fragments for tool lifecycle events:
start (▶ dim cyan), success (✓ bold green), failure (✗ bold red).
Tool name is emphasized, args are dim, elapsed time is right-aligned.
"""
from __future__ import annotations

from typing import Any, Dict

from rich.text import Text

from llm_code.view.repl import style

__all__ = ["render_start", "render_success", "render_failure"]


def _truncate_args(args: Dict[str, Any], max_len: int = 40) -> str:
    if not args:
        return ""
    parts = []
    for k, v in args.items():
        rendered = f"{k}={v}"
        if len(rendered) > max_len:
            rendered = rendered[: max_len - 1] + "…"
        parts.append(rendered)
    joined = ", ".join(parts)
    if len(joined) > max_len:
        return joined[: max_len - 1] + "…"
    return joined


def render_start(tool: str, args: Dict[str, Any]) -> Text:
    out = Text()
    out.append(f"{style.ICON_START} ", style=style.palette.tool_start_fg)
    out.append(tool, style=style.palette.tool_name_fg)
    args_text = _truncate_args(args)
    if args_text:
        out.append(" ", style="")
        out.append(args_text, style=style.palette.tool_args_fg)
    return out


def render_success(
    tool: str, summary: str = "", elapsed: float | None = None
) -> Text:
    out = Text()
    out.append(f"{style.ICON_SUCCESS} ", style=style.palette.tool_ok_fg)
    out.append(tool, style=style.palette.tool_name_fg)
    if summary:
        out.append(" ", style="")
        out.append(summary, style=style.palette.system_fg)
    if elapsed is not None:
        out.append(f"  ({elapsed:.1f}s)", style=style.palette.tool_elapsed_fg)
    return out


def render_failure(
    tool: str,
    error: str,
    elapsed: float | None = None,
    exit_code: int | None = None,
) -> Text:
    out = Text()
    out.append(f"{style.ICON_FAILURE} ", style=style.palette.tool_fail_fg)
    out.append(tool, style=style.palette.tool_name_fg)
    if exit_code is not None:
        out.append(f" (exit {exit_code})", style=style.palette.tool_args_fg)
    out.append("  ", style="")
    out.append(error, style=style.palette.status_error)
    if elapsed is not None:
        out.append(f"  ({elapsed:.1f}s)", style=style.palette.tool_elapsed_fg)
    return out
