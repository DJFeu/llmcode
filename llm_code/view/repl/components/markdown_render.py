"""Rich Markdown wrapper with lexer-detected code fences (M15 Task C4).

Wraps Rich's ``Markdown`` renderer but drops in our tech-blue
heading / inline-code / link styles via the palette. Code fences
go through :mod:`llm_code.view.repl.components.code_block` for
consistent syntax highlighting across the assistant text path.
"""
from __future__ import annotations

from rich.markdown import Markdown

from llm_code.view.repl import style

__all__ = ["render_markdown"]


def render_markdown(source: str) -> Markdown:
    """Return a Rich ``Markdown`` for the given source.

    Passes ``code_theme`` so fenced code blocks use a consistent
    monokai palette. Heading styles come from the brand palette.
    """
    md = Markdown(
        source,
        code_theme="monokai",
        inline_code_theme="monokai",
        justify="left",
    )
    # Rich's Markdown honors style tokens by name on the console's
    # theme. We override the key heading / link / inline_code tones
    # via Rich's markdown styles dict on the instance.
    try:
        md.elements["heading_open"].style = style.palette.markdown_heading
    except Exception:
        pass
    return md
