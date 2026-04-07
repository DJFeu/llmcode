"""Per-tool argument formatting for ToolBlock headers.

Replaces fragile regex extraction with a tool-name -> formatter registry.
"""
from __future__ import annotations

import ast
import os
from typing import Any, Callable
from urllib.parse import urlparse

_MAX = 60


def _truncate(s: str, n: int = _MAX) -> str:
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def _parse_args(args: str | dict) -> dict | str:
    """Try hard to turn args into a dict; fall back to original string."""
    if isinstance(args, dict):
        return args
    if not isinstance(args, str):
        return str(args)
    s = args.strip()
    if not s:
        return {}
    try:
        parsed = ast.literal_eval(s)
        if isinstance(parsed, dict):
            return parsed
    except (ValueError, SyntaxError):
        pass
    return s


def _pick_path(d: dict) -> str:
    for key in ("file_path", "path", "notebook_path"):
        v = d.get(key)
        if isinstance(v, str) and v:
            return v
    return ""


def _format_path(d: dict, verbose: bool) -> str:
    path = _pick_path(d)
    if not path:
        return ""
    if not verbose and len(path) > _MAX:
        return os.path.basename(path) or path[-_MAX:]
    return path


def _fmt_file(args: dict | str, verbose: bool) -> str:
    if isinstance(args, dict):
        return _format_path(args, verbose) or _fallback(args)
    return _truncate(args)


def _fmt_bash(args: dict | str, verbose: bool) -> str:
    if isinstance(args, dict):
        cmd = args.get("command") or args.get("cmd") or ""
        if not isinstance(cmd, str):
            cmd = str(cmd)
    else:
        cmd = args
    return "$ " + _truncate(cmd.strip(), _MAX)


def _fmt_glob(args: dict | str, verbose: bool) -> str:
    if isinstance(args, dict):
        pattern = args.get("pattern") or args.get("glob") or ""
        return _truncate(str(pattern))
    return _truncate(args)


def _fmt_grep(args: dict | str, verbose: bool) -> str:
    if isinstance(args, dict):
        pattern = str(args.get("pattern", ""))
        path = str(args.get("path", "") or args.get("dir", ""))
        if path:
            return _truncate(f"{pattern} in {path}")
        return _truncate(pattern)
    return _truncate(args)


def _fmt_web_fetch(args: dict | str, verbose: bool) -> str:
    if isinstance(args, dict):
        url = str(args.get("url", ""))
    else:
        url = args
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        host = parsed.netloc or url
        path = parsed.path or ""
        combined = host + path
        return _truncate(combined)
    except Exception:
        return _truncate(url)


def _fmt_web_search(args: dict | str, verbose: bool) -> str:
    if isinstance(args, dict):
        q = str(args.get("query", "") or args.get("q", ""))
        return _truncate(q)
    return _truncate(args)


def _fallback(args: dict | str) -> str:
    return _truncate(str(args))


_REGISTRY: dict[str, Callable[[Any, bool], str]] = {
    "read_file": _fmt_file,
    "write_file": _fmt_file,
    "edit_file": _fmt_file,
    "notebook_read": _fmt_file,
    "notebook_edit": _fmt_file,
    "bash": _fmt_bash,
    "glob_search": _fmt_glob,
    "grep_search": _fmt_grep,
    "web_fetch": _fmt_web_fetch,
    "web_search": _fmt_web_search,
}


def render_tool_args(
    tool_name: str,
    args: str | dict,
    verbose: bool = False,
) -> str:
    """Format a tool call's argument preview for ToolBlock headers.

    Never raises on malformed input.
    """
    try:
        parsed = _parse_args(args)
        formatter = _REGISTRY.get(tool_name)
        if formatter is None:
            return _fallback(parsed)
        return formatter(parsed, verbose)
    except Exception:
        return _fallback(args)
