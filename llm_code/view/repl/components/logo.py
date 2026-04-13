"""LLMCODE block-letter gradient logo with box-drawing 3D style (M15 A2).

Uses Unicode box-drawing characters (``╔╗╚╝║═``) to render each
letter with an outlined, beveled look — matching the classic
figlet/toilet "ANSI Shadow" aesthetic the user approved. The logo
is split across two lines: ``LLM`` on top, ``CODE`` below.

Top-to-bottom tech-blue gradient is applied per-row across both
halves. All colors are read via ``palette.*`` so a theme override
re-tints the logo in one shot.
"""
from __future__ import annotations

from rich.text import Text

from llm_code.view.repl import style

__all__ = [
    "render_llmcode_logo",
    "render_llmcode_logo_compact",
    "LOGO_HEIGHT",
    "LOGO_WIDTH",
]

# ── Raw glyph data ──────────────────────────────────────────────
# Each letter is 6 rows. Two groups: "LLM" and "CODE".
# Using box-drawing chars: ██ ╗ ╔ ╝ ╚ ║ ═ ╔═ ══ ═╗ etc.

_LLM_ROWS = [
    " ██╗      ██╗      ███╗   ███╗",
    " ██║      ██║      ████╗ ████║",
    " ██║      ██║      ██╔████╔██║",
    " ██║      ██║      ██║╚██╔╝██║",
    " ███████╗ ███████╗ ██║ ╚═╝ ██║",
    " ╚══════╝ ╚══════╝ ╚═╝     ╚═╝",
]

_CODE_ROWS = [
    "  ██████╗  ██████╗  ██████╗  ███████╗",
    " ██╔════╝ ██╔═══██╗ ██╔══██╗ ██╔════╝",
    " ██║      ██║   ██║ ██║  ██║ █████╗  ",
    " ██║      ██║   ██║ ██║  ██║ ██╔══╝  ",
    " ╚██████╗ ╚██████╔╝ ██████╔╝ ███████╗",
    "  ╚═════╝  ╚═════╝  ╚═════╝  ╚══════╝",
]

LOGO_HEIGHT = len(_LLM_ROWS) + len(_CODE_ROWS)  # 12
LOGO_WIDTH = max(
    max(len(r) for r in _LLM_ROWS),
    max(len(r) for r in _CODE_ROWS),
)

# 12-row gradient ramp: repeat each of the 5 stops so the gradient
# spans the full height smoothly (top = lightest, bottom = deepest).
_GRADIENT_STOPS = 12


def _row_color(row: int) -> str:
    """Map a row index (0..11) to a gradient color."""
    ramp = (
        style.palette.llmcode_blue_hilite,
        style.palette.llmcode_blue_hilite,
        style.palette.llmcode_blue_light,
        style.palette.llmcode_blue_light,
        style.palette.llmcode_blue_mid,
        style.palette.llmcode_blue_mid,
        style.palette.llmcode_blue_dark,
        style.palette.llmcode_blue_dark,
        style.palette.llmcode_blue_deep,
        style.palette.llmcode_blue_deep,
        style.palette.llmcode_blue_deep,
        style.palette.llmcode_blue_deep,
    )
    return ramp[max(0, min(row, len(ramp) - 1))]


def render_llmcode_logo() -> Text:
    """Return the full LLMCODE banner as Rich ``Text``.

    Two halves (LLM + CODE), each 6 rows, with per-row gradient.
    Box-drawing chars (``╔═╗║╚═╝``) give the 3D beveled outline.
    """
    all_rows = _LLM_ROWS + _CODE_ROWS
    text = Text(no_wrap=True, overflow="ignore")
    for i, row in enumerate(all_rows):
        color = _row_color(i)
        text.append(row, style=f"bold {color}")
        if i < len(all_rows) - 1:
            text.append("\n")
    return text


def render_llmcode_logo_compact() -> Text:
    """1-row bold tech-blue ``llmcode`` label for small terminals."""
    return Text("llmcode", style=f"bold {style.palette.llmcode_blue_mid}")
