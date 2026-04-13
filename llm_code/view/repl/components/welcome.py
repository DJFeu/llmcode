"""Rich welcome panel renderer for the v2.0.0 REPL (M15 Task A3).

Combines the LLMCODE block-letter gradient logo with an info grid
(model / cwd / permission / thinking) inside a Rich Panel whose
border uses ``palette.brand_accent`` (tech-blue by default, re-
tinted by a user theme override via :mod:`llm_code.view.repl.style`).

The renderer is a pure helper: it takes the scalar fields it
needs and returns a Rich renderable. Wiring lives in
``cli/main._print_welcome``.
"""
from __future__ import annotations

from typing import Optional

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from llm_code.view.repl import style
from llm_code.view.repl.components.logo import (
    render_llmcode_logo,
    render_llmcode_logo_compact,
)

__all__ = ["render_welcome_panel"]


# Rows required for the full 5-row banner + info grid + padding.
# Below this, fall back to the compact single-row logo.
_FULL_LOGO_MIN_ROWS = 20


def _build_info_table(
    *,
    model: str,
    cwd: str,
    permission_mode: Optional[str],
    thinking_mode: Optional[str],
) -> Table:
    info = Table.grid(padding=(0, 2), expand=False)
    info.add_column(justify="right", style=f"bold {style.palette.brand_accent}", no_wrap=True)
    info.add_column(justify="left", no_wrap=False, style=style.palette.system_fg)

    info.add_row("model", model)
    info.add_row("cwd", cwd)
    if permission_mode:
        info.add_row("permission", permission_mode)
    if thinking_mode:
        info.add_row("thinking", thinking_mode)
    return info


def render_welcome_panel(
    *,
    version: str,
    model: str,
    cwd: str,
    permission_mode: Optional[str] = None,
    thinking_mode: Optional[str] = None,
    terminal_rows: Optional[int] = None,
) -> Panel:
    """Return a Rich ``Panel`` with the full welcome layout.

    Parameters
    ----------
    terminal_rows:
        Current terminal height. When unset or < ``_FULL_LOGO_MIN_ROWS``,
        the compact single-row logo is used instead of the 5-row banner
        so we don't eat half the viewport on small splits.
    """
    compact = terminal_rows is not None and terminal_rows < _FULL_LOGO_MIN_ROWS
    logo: RenderableType = (
        render_llmcode_logo_compact() if compact else render_llmcode_logo()
    )

    info = _build_info_table(
        model=model,
        cwd=cwd,
        permission_mode=permission_mode,
        thinking_mode=thinking_mode,
    )

    hint = Text(
        "Ctrl+G voice  ·  /  commands  ·  Ctrl+D quit",
        style=style.palette.hint_fg,
        justify="center",
    )

    body = Group(logo, Text(""), info, Text(""), hint)

    return Panel(
        body,
        title=f"[bold {style.palette.brand_accent}]llmcode v{version}[/]",
        title_align="center",
        border_style=f"bold {style.palette.brand_accent}",
        padding=(1, 2),
        expand=True,
    )
