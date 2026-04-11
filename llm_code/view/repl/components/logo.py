"""LLMCODE block-letter gradient logo (M15 Task A2).

Renders the word "LLMCODE" as 5-row block-character art with a
top-to-bottom tech-blue gradient, emulating the Hermes-agent
pixelated block-letter style. A subtle diagonal drop-shadow tone
gives each glyph a 3D outline without per-letter hand-tuning.

llmcode keeps its own visual identity — this logo is not derived
from or intended to resemble Claude Code's mascot.

Public API
----------
- :func:`render_llmcode_logo` — full 5-row banner (default)
- :func:`render_llmcode_logo_compact` — 1-row fallback for
  terminals that are too short for the full banner

Both read colors through :mod:`llm_code.view.repl.style.palette`
so a user theme override re-tints the logo in one shot.
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

# 5 rows × 5 cols per glyph + 1-col kerning between letters.
_GLYPH_ROWS = 5
_GLYPH_COLS = 5
LOGO_HEIGHT = _GLYPH_ROWS

# -----------------------------------------------------------------
# 5×5 block-letter templates for the seven glyphs in "LLMCODE".
#
# ``█`` = solid body, `` `` = empty. Each row is exactly five
# characters. Kerning is one space column added between glyphs at
# render time so we don't repeat it inside every template.
# -----------------------------------------------------------------

_GLYPHS: dict[str, list[str]] = {
    "L": [
        "█    ",
        "█    ",
        "█    ",
        "█    ",
        "█████",
    ],
    "M": [
        "█   █",
        "██ ██",
        "█ █ █",
        "█   █",
        "█   █",
    ],
    "C": [
        " ████",
        "█    ",
        "█    ",
        "█    ",
        " ████",
    ],
    "O": [
        " ███ ",
        "█   █",
        "█   █",
        "█   █",
        " ███ ",
    ],
    "D": [
        "████ ",
        "█   █",
        "█   █",
        "█   █",
        "████ ",
    ],
    "E": [
        "█████",
        "█    ",
        "████ ",
        "█    ",
        "█████",
    ],
}

_WORD = "LLMCODE"
_KERN = 1  # columns of space between glyphs
LOGO_WIDTH = len(_WORD) * _GLYPH_COLS + (len(_WORD) - 1) * _KERN


def _row_color(row: int) -> str:
    """Return the brand gradient color for a given logo row (0..4)."""
    ramp = (
        style.palette.llmcode_blue_hilite,
        style.palette.llmcode_blue_light,
        style.palette.llmcode_blue_mid,
        style.palette.llmcode_blue_dark,
        style.palette.llmcode_blue_deep,
    )
    return ramp[max(0, min(row, len(ramp) - 1))]


def _compose_grid() -> list[list[str]]:
    """Return a 2-D grid of chars with ``█`` or space per cell."""
    grid: list[list[str]] = [[" "] * LOGO_WIDTH for _ in range(_GLYPH_ROWS)]
    col_cursor = 0
    for i, letter in enumerate(_WORD):
        glyph = _GLYPHS[letter]
        for r in range(_GLYPH_ROWS):
            row_chars = glyph[r]
            for c in range(_GLYPH_COLS):
                grid[r][col_cursor + c] = row_chars[c]
        col_cursor += _GLYPH_COLS + _KERN
    return grid


def render_llmcode_logo() -> Text:
    """Return the full 5-row ``LLMCODE`` banner as a Rich ``Text``.

    Each row is styled with the corresponding brand gradient stop;
    diagonal drop-shadow cells (a solid cell whose bottom-right
    diagonal is empty AND whose direct-below neighbor is empty)
    emit a shadow-toned character below-right to create a subtle
    3D outline.
    """
    grid = _compose_grid()
    text = Text(no_wrap=True, overflow="ignore")

    # Render the main body rows first.
    for r in range(_GLYPH_ROWS):
        line_color = _row_color(r)
        for c in range(LOGO_WIDTH):
            ch = grid[r][c]
            if ch == "█":
                text.append("█", style=f"bold {line_color}")
            else:
                # Check if we should drop a shadow cell here.
                if r > 0 and c > 0 and grid[r - 1][c - 1] == "█":
                    # Diagonal below-right of a solid cell and current
                    # cell is empty: place a deeper shadow tone.
                    text.append("▒", style=style.palette.logo_shadow_fg)
                else:
                    text.append(" ")
        if r < _GLYPH_ROWS - 1:
            text.append("\n")
    return text


def render_llmcode_logo_compact() -> Text:
    """Return a 1-row bold tech-blue ``llmcode`` label.

    Used when the terminal is too short for the full 5-row banner
    (e.g. cold start in a split pane with fewer than 20 rows).
    """
    return Text("llmcode", style=f"bold {style.palette.llmcode_blue_mid}")
