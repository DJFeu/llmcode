"""LLMCODE block-letter gradient logo with 3D extrusion (M15 Task A2).

Renders the word "LLMCODE" as 6-row block-character art (5 body
rows + 1 shadow tail) with:

1. **Top-to-bottom gradient**: 5-stop tech-blue ramp (hilite →
   deep) across the body rows.
2. **3D extrusion shadow**: the full letter body is duplicated 1
   row down + 1 col right in a very dark ``logo_shadow_fg`` tone,
   rendering behind the body to create an embossed/extruded look.
3. **Top highlight edge**: the first solid cell of each letter's
   top row uses ``▀`` (upper half-block) in the lightest gradient
   stop to add a sharp top bevel.

The composite grid is 6 rows tall (``LOGO_HEIGHT``) and ~42 cols
wide. Both ``render_llmcode_logo`` and ``render_llmcode_logo_compact``
read colors through ``style.palette`` so a user theme override
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

_GLYPH_ROWS = 5
_GLYPH_COLS = 5
# Total visible height = body rows + 1 shadow tail row
LOGO_HEIGHT = _GLYPH_ROWS + 1

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
_KERN = 1
# Width includes 1-col shadow offset on the right
LOGO_WIDTH = len(_WORD) * _GLYPH_COLS + (len(_WORD) - 1) * _KERN + 1


def _row_color(row: int) -> str:
    ramp = (
        style.palette.llmcode_blue_hilite,
        style.palette.llmcode_blue_light,
        style.palette.llmcode_blue_mid,
        style.palette.llmcode_blue_dark,
        style.palette.llmcode_blue_deep,
    )
    return ramp[max(0, min(row, len(ramp) - 1))]


def _compose_body_grid() -> list[list[str]]:
    """Return a 5-row × (LOGO_WIDTH-1) body grid."""
    w = LOGO_WIDTH - 1  # body grid excludes shadow column
    grid: list[list[str]] = [[" "] * w for _ in range(_GLYPH_ROWS)]
    col_cursor = 0
    for letter in _WORD:
        glyph = _GLYPHS[letter]
        for r in range(_GLYPH_ROWS):
            for c in range(_GLYPH_COLS):
                if col_cursor + c < w:
                    grid[r][col_cursor + c] = glyph[r][c]
        col_cursor += _GLYPH_COLS + _KERN
    return grid


def render_llmcode_logo() -> Text:
    """Return the full LLMCODE banner with 3D extrusion shadow."""
    body = _compose_body_grid()
    body_w = len(body[0]) if body else 0
    text = Text(no_wrap=True, overflow="ignore")

    # Composite 6 rows: row 0..4 are body+shadow overlay, row 5 is
    # shadow tail only (the bottom edge of the extrusion).
    total_rows = _GLYPH_ROWS + 1
    shadow_fg = style.palette.logo_shadow_fg

    for r in range(total_rows):
        for c in range(LOGO_WIDTH):
            # Body cell (if within body bounds)
            has_body = (
                r < _GLYPH_ROWS
                and c < body_w
                and body[r][c] == "█"
            )
            # Shadow cell: body[r-1][c-1] was solid (shadow is
            # the body shifted 1 down + 1 right)
            has_shadow = (
                r >= 1
                and c >= 1
                and (r - 1) < _GLYPH_ROWS
                and (c - 1) < body_w
                and body[r - 1][c - 1] == "█"
            )

            if has_body:
                # Body wins over shadow — renders in gradient
                line_color = _row_color(r)
                text.append("█", style=f"bold {line_color}")
            elif has_shadow:
                # Shadow layer behind/below the body
                text.append("█", style=shadow_fg)
            else:
                text.append(" ")
        if r < total_rows - 1:
            text.append("\n")
    return text


def render_llmcode_logo_compact() -> Text:
    """Return a 1-row bold tech-blue ``llmcode`` label."""
    return Text("llmcode", style=f"bold {style.palette.llmcode_blue_mid}")
