"""Colored tool-event progress line — Claude Code style (M15 Task D1).

Claude Code uses:
- Tool name in bold white (terminal default fg)
- ``⎿`` (U+23BF) hook in dimColor for indented progress/result
- ✓ in success green ``rgb(78,186,101)``
- ✗ in error red ``rgb(255,107,128)``
- Args and elapsed in "subtle" ``rgb(80,80,80)``
- One blank line between tool blocks (not a divider)
"""
from __future__ import annotations

from typing import Any, Dict

from rich.text import Text

from llm_code.view.repl import style

__all__ = ["render_start", "render_success", "render_failure", "render_progress_hook"]

# Claude Code's indented progress hook character
HOOK = "⎿"


def _truncate_args(args: Dict[str, Any], max_len: int = 60) -> str:
    if not args:
        return ""
    # Priority display: path / file / command / query
    priority = ("path", "file", "filepath", "command", "cmd", "query", "url")
    for key in priority:
        if key in args and args[key]:
            value = str(args[key])
            if len(value) > max_len:
                return value[: max_len - 1] + "…"
            return value
    # Fallback: first key=value
    parts = []
    for k, v in args.items():
        if k in priority:
            continue
        rendered = f"{k}={v}"
        if len(rendered) > max_len:
            rendered = rendered[: max_len - 1] + "…"
        parts.append(rendered)
        break
    return ", ".join(parts)


def render_start(tool: str, args: Dict[str, Any]) -> Text:
    """Tool start line: ``tool_name  args_summary``."""
    out = Text()
    out.append(tool, style=f"bold {style.palette.tool_name_fg}")
    args_text = _truncate_args(args)
    if args_text:
        out.append("  ", style="")
        out.append(args_text, style=style.palette.tool_args_fg)
    return out


def render_progress_hook(message: str) -> Text:
    """Indented progress line: ``  ⎿  message`` in dim."""
    out = Text()
    out.append(f"  {HOOK}  ", style=style.palette.tool_progress_hook)
    out.append(message, style=style.palette.tool_args_fg)
    return out


def render_success(
    tool: str, summary: str = "", elapsed: float | None = None
) -> Text:
    """Success line: ``  ⎿  ✓ summary  (Ns)``."""
    out = Text()
    out.append(f"  {HOOK}  ", style=style.palette.tool_progress_hook)
    out.append(f"{style.ICON_SUCCESS} ", style=f"bold {style.palette.tool_ok_fg}")
    if summary:
        out.append(summary, style=style.palette.system_fg)
    else:
        out.append(tool, style=style.palette.system_fg)
    if elapsed is not None:
        out.append(f"  ({elapsed:.1f}s)", style=style.palette.tool_elapsed_fg)
    return out


def render_failure(
    tool: str,
    error: str,
    elapsed: float | None = None,
    exit_code: int | None = None,
) -> Text:
    """Failure line: ``  ⎿  ✗ error  (Ns)  exit N``."""
    out = Text()
    out.append(f"  {HOOK}  ", style=style.palette.tool_progress_hook)
    out.append(f"{style.ICON_FAILURE} ", style=f"bold {style.palette.tool_fail_fg}")
    out.append(error, style=style.palette.tool_fail_fg)
    if exit_code is not None:
        out.append(f"  (exit {exit_code})", style=style.palette.tool_args_fg)
    if elapsed is not None:
        out.append(f"  ({elapsed:.1f}s)", style=style.palette.tool_elapsed_fg)
    return out
