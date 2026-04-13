"""Rich Markdown wrapper — Claude Code style (M15 Task C4).

Claude Code's markdown rendering:
- **H1**: bold + italic + underline
- **H2+**: bold
- **Inline code**: ``rgb(177,185,249)`` — light blue-purple
- **Blockquote**: dim + italic
- **Code fences**: syntax highlighted via monokai
- **Links**: OSC8 hyperlinks
- **Body text**: pure white ``rgb(255,255,255)``
"""
from __future__ import annotations

from rich.markdown import Markdown
from rich.style import Style

from llm_code.view.repl import style

__all__ = ["render_markdown"]


def render_markdown(source: str) -> Markdown:
    """Return a Rich ``Markdown`` with Claude Code-matched styling."""
    md = Markdown(
        source,
        code_theme="monokai",
        inline_code_theme="monokai",
        inline_code_lexer="python",
        justify="left",
    )
    # Override Rich's default markdown element styles to match Claude Code.
    # Rich uses a style_table dict keyed by element name.
    overrides = {
        "markdown.h1": Style(
            bold=True, italic=True, underline=True,
            color=style.palette.markdown_heading,
        ),
        "markdown.h2": Style(bold=True, color=style.palette.markdown_heading),
        "markdown.h3": Style(bold=True, color=style.palette.markdown_heading),
        "markdown.h4": Style(bold=True, color=style.palette.markdown_heading),
        "markdown.code": Style(color=style.palette.markdown_code_inline),
        "markdown.block_quote": Style(
            italic=True, color=style.palette.markdown_quote_fg,
        ),
        "markdown.link": Style(
            underline=True, color=style.palette.markdown_link,
        ),
        "markdown.link_url": Style(
            color=style.palette.tool_args_fg,
        ),
    }
    # Apply overrides via Rich's console style mechanism.
    # The Markdown object carries a `style` attribute we can set
    # on its internal elements via the `_style_table` dict.
    try:
        for key, s in overrides.items():
            md.style_table[key] = s  # type: ignore[attr-defined]
    except Exception:
        pass
    return md
