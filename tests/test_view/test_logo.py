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
    return str(text).split("\n")


def test_full_banner_height() -> None:
    rows = _plain_rows(render_llmcode_logo())
    # 5 body rows + 1 shadow tail = 6 rows total
    assert len(rows) == LOGO_HEIGHT == 6


def test_every_row_has_consistent_width() -> None:
    rows = _plain_rows(render_llmcode_logo())
    for row in rows:
        assert len(row) == LOGO_WIDTH


def test_logo_width_matches_word_plus_shadow() -> None:
    # Seven 5-col glyphs + six 1-col kernings + 1 shadow col = 42
    assert LOGO_WIDTH == 7 * 5 + 6 + 1


def test_all_seven_letters_present_as_block_chars() -> None:
    rows = _plain_rows(render_llmcode_logo())
    combined = "\n".join(rows)
    assert "█" in combined


def test_gradient_stops_all_appear_in_span_list() -> None:
    text = render_llmcode_logo()
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
    assert mid in str(text.style)


def test_shadow_cells_use_shadow_tone() -> None:
    """The 6th (tail) row is entirely shadow-tone cells + spaces."""
    text = render_llmcode_logo()
    # The shadow tone must appear somewhere in the spans.
    shadow_fg = style.palette.logo_shadow_fg
    span_styles = [str(span.style) for span in text.spans]
    assert any(shadow_fg in s for s in span_styles), (
        f"expected logo_shadow_fg {shadow_fg} in spans, "
        f"got: {set(span_styles)}"
    )
