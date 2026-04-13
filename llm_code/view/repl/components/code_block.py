"""Code block helper with lexer autodetection (M15 Task D3).

Thin wrapper around ``rich.syntax.Syntax`` that prefers an
explicit language hint, falls back to
:meth:`Syntax.guess_lexer` on the filename / code body when the
hint is missing, and applies a consistent ``monokai`` theme.
Used by :mod:`markdown_render` and :mod:`structured_diff`.
"""
from __future__ import annotations

from rich.syntax import Syntax

__all__ = ["render_syntax"]


def render_syntax(
    code: str,
    *,
    language: str | None = None,
    filename: str | None = None,
    line_numbers: bool = False,
    start_line: int = 1,
) -> Syntax:
    lexer: str
    if language:
        lexer = language
    else:
        lexer = _guess_lexer(filename or "untitled.txt", code)
    return Syntax(
        code,
        lexer,
        theme="monokai",
        line_numbers=line_numbers,
        start_line=start_line,
        word_wrap=False,
    )


def _guess_lexer(filename: str, code: str) -> str:
    try:
        return Syntax.guess_lexer(filename, code=code)
    except Exception:
        return "text"
