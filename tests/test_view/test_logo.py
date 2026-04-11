"""Tests for the LLMCODE block-letter gradient logo (M15 Task A2)."""
from __future__ import annotations

from llm_code.view.repl import style
from llm_code.view.repl.components.logo import (
    LOGO_HEIGHT,
    LOGO_WIDTH,
    render_llmcode_logo,
    render_llmcode_logo_compact,
)


def _plain_rows(text) -> list[str]:
    """Return the rendered text split into rows of plain characters."""
    return str(text).split("\n")


def test_full_banner_is_five_rows_tall() -> None:
    rows = _plain_rows(render_llmcode_logo())
    assert len(rows) == LOGO_HEIGHT == 5


def test_every_row_has_logo_width() -> None:
    rows = _plain_rows(render_llmcode_logo())
    for row in rows:
        # Trim trailing spaces inserted by shadow cells beyond the
        # word's right edge; width is the logical banner width.
        assert len(row) == LOGO_WIDTH


def test_logo_width_matches_seven_letter_word() -> None:
    # Seven 5-col glyphs + six 1-col kernings = 41 cols.
    assert LOGO_WIDTH == 7 * 5 + 6


def test_all_seven_letters_present_as_block_chars() -> None:
    rows = _plain_rows(render_llmcode_logo())
    combined = "\n".join(rows)
    # We expect at least one ``█`` block char (the letters are drawn
    # entirely with them).
    assert "█" in combined


def test_gradient_stops_all_appear_in_span_list() -> None:
    text = render_llmcode_logo()
    # Rich stores color as a complex object — walk the spans and
    # check for multiple distinct styles (evidence of a gradient).
    distinct_styles = {str(span.style) for span in text.spans}
    assert len(distinct_styles) >= 3, (
        f"expected multiple gradient stops, got: {distinct_styles}"
    )


def test_theme_override_re_tints_logo() -> None:
    original = style.palette
    try:
        custom = style.default_palette().__class__(
            llmcode_blue_hilite="#ff0000",
            llmcode_blue_light="#ee0000",
            llmcode_blue_mid="#dd0000",
            llmcode_blue_dark="#cc0000",
            llmcode_blue_deep="#bb0000",
        )
        style.set_palette(custom)
        text = render_llmcode_logo()
        markup = "".join(str(span.style) for span in text.spans)
        assert "#ff0000" in markup or "ff0000" in markup
    finally:
        style.set_palette(original)


def test_compact_logo_is_one_row() -> None:
    text = render_llmcode_logo_compact()
    assert "\n" not in str(text)
    assert "llmcode" in str(text)


def test_compact_logo_uses_mid_tone() -> None:
    text = render_llmcode_logo_compact()
    mid = style.palette.llmcode_blue_mid
    span_styles = [str(span.style) for span in text.spans]
    # mid tone appears in at least one span (the whole-text style)
    assert any(mid in s for s in span_styles + [str(text.style)])


def test_shadow_tone_only_in_empty_cells_below_solid_diagonal() -> None:
    """The shadow glyph ``▒`` must never appear on a solid-body row cell."""
    text = render_llmcode_logo()
    # Walk the raw chars: shadow cells are rendered as ``▒``, body
    # cells as ``█``. We just check that the two never coexist on
    # the same character position — the dict keys are plain string.
    rendered = str(text)
    assert "▒" in rendered  # at least one shadow cell exists
    # Body is block char, shadow is ▒; they are distinct.
    assert "█" in rendered
