"""Tests for the LLMCODE box-drawing gradient logo (M15 Task A2)."""
from __future__ import annotations

from llm_code.view.repl import style
from llm_code.view.repl.components.logo import (
    LOGO_HEIGHT,
    render_llmcode_logo,
    render_llmcode_logo_compact,
)


def _plain_rows(text) -> list[str]:
    return str(text).split("\n")


def test_full_banner_is_12_rows() -> None:
    rows = _plain_rows(render_llmcode_logo())
    assert len(rows) == LOGO_HEIGHT == 12


def test_banner_contains_box_drawing_chars() -> None:
    rendered = str(render_llmcode_logo())
    for ch in ("╔", "╗", "╚", "╝", "║", "═"):
        assert ch in rendered, f"expected box-drawing char {ch!r}"


def test_banner_contains_block_chars() -> None:
    rendered = str(render_llmcode_logo())
    assert "██" in rendered


def test_gradient_has_multiple_distinct_stops() -> None:
    text = render_llmcode_logo()
    distinct_styles = {str(span.style) for span in text.spans}
    assert len(distinct_styles) >= 3


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
