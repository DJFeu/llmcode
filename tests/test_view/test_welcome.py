"""Tests for the M15 welcome panel (Task A3)."""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel

from llm_code.view.repl import style
from llm_code.view.repl.components.welcome import render_welcome_panel


def _render(panel: Panel, width: int = 80) -> str:
    console = Console(width=width, record=True, color_system="truecolor")
    console.print(panel)
    return console.export_text()


def test_welcome_panel_returns_panel_instance() -> None:
    panel = render_welcome_panel(
        version="2.0.0",
        model="Qwen3.5-122B-A10B-int4",
        cwd="llm-code",
    )
    assert isinstance(panel, Panel)


def test_welcome_panel_includes_version_and_model() -> None:
    text = _render(
        render_welcome_panel(
            version="2.0.0",
            model="Qwen3.5-122B-A10B-int4",
            cwd="llm-code",
        )
    )
    assert "llmcode v2.0.0" in text
    assert "Qwen3.5-122B-A10B-int4" in text
    assert "llm-code" in text


def test_welcome_panel_includes_optional_fields() -> None:
    text = _render(
        render_welcome_panel(
            version="2.0.0",
            model="test-model",
            cwd="/tmp",
            permission_mode="yolo",
            thinking_mode="adaptive",
        )
    )
    assert "permission" in text
    assert "yolo" in text
    assert "thinking" in text
    assert "adaptive" in text


def test_welcome_panel_full_logo_on_large_terminal() -> None:
    text = _render(
        render_welcome_panel(
            version="2.0.0",
            model="m",
            cwd="/",
            terminal_rows=40,
        )
    )
    # Full banner uses ``█`` block chars; compact is plain "llmcode".
    assert "█" in text


def test_welcome_panel_compact_logo_on_small_terminal() -> None:
    text = _render(
        render_welcome_panel(
            version="2.0.0",
            model="m",
            cwd="/",
            terminal_rows=10,
        )
    )
    # Compact fallback path — no block chars.
    assert "█" not in text
    assert "llmcode" in text


def test_welcome_panel_hint_row_present() -> None:
    text = _render(
        render_welcome_panel(version="2.0.0", model="m", cwd="/")
    )
    assert "Ctrl+G" in text
    assert "Ctrl+D" in text


def test_welcome_panel_respects_theme_override() -> None:
    original = style.palette
    try:
        custom = style.default_palette().__class__(brand_accent="#ff00ff")
        style.set_palette(custom)
        panel = render_welcome_panel(version="2.0.0", model="m", cwd="/")
        assert "#ff00ff" in str(panel.border_style)
    finally:
        style.set_palette(original)
