"""Tests for the context meter (M15 Task A5)."""
from __future__ import annotations

from llm_code.view.repl import style
from llm_code.view.repl.components.context_meter import render_context_meter


def _flat(out) -> str:
    return "".join(text for _, text in out)


def test_returns_label_and_bar() -> None:
    out = render_context_meter(1000, 4000)
    assert len(out) == 2
    assert "1000/4000 tok" in _flat(out)


def test_low_fill_uses_success_color() -> None:
    out = render_context_meter(100, 1000)
    _, bar_style = out[1][0], out[1][0]
    assert style.palette.status_success in out[1][0]


def test_mid_fill_uses_warning_color() -> None:
    out = render_context_meter(700, 1000)
    assert style.palette.status_warning in out[1][0]


def test_high_fill_uses_error_color() -> None:
    out = render_context_meter(900, 1000)
    assert style.palette.status_error in out[1][0]


def test_bar_has_five_cells() -> None:
    out = render_context_meter(500, 1000)
    bar = out[1][1]
    assert len(bar) == 5


def test_zero_limit_fallback() -> None:
    out = render_context_meter(100, 0)
    assert "?" in _flat(out)


def test_bar_fills_proportionally() -> None:
    # 0% → no solid blocks
    _, (_, empty_bar) = render_context_meter(0, 1000)
    # 100% → all solid blocks
    _, (_, full_bar) = render_context_meter(1000, 1000)
    assert full_bar.count("█") == 5
    # Empty bar is the graded stack, no solid blocks
    assert empty_bar.count("█") == 0
