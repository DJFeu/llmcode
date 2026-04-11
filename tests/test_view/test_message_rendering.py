"""Tests for M15 Group C message rendering components (C1-C5)."""
from __future__ import annotations

from rich.console import Console

from llm_code.view.repl import style
from llm_code.view.repl.components.assistant_text import render_assistant_text
from llm_code.view.repl.components.compact_summary import render_compact_summary
from llm_code.view.repl.components.markdown_render import render_markdown
from llm_code.view.repl.components.thinking_render import render_thinking
from llm_code.view.repl.components.user_prompt_echo import render_user_prompt_echo


def _render(renderable) -> str:
    console = Console(width=80, record=True, color_system="truecolor")
    console.print(renderable)
    return console.export_text()


# === C1 Assistant text ===


def test_assistant_text_has_bullet_prefix() -> None:
    out = _render(render_assistant_text("Hello world"))
    assert out.startswith("● ")
    assert "Hello world" in out


def test_assistant_text_respects_theme_override() -> None:
    original = style.palette
    try:
        custom = style.default_palette().__class__(assistant_bullet="#ff0000")
        style.set_palette(custom)
        text = render_assistant_text("hi")
        assert any("#ff0000" in str(s.style) for s in text.spans)
    finally:
        style.set_palette(original)


# === C2 User prompt echo ===


def test_user_prompt_echo_has_prefix() -> None:
    out = _render(render_user_prompt_echo("What's up?"))
    assert out.startswith("> ")
    assert "What's up?" in out


# === C3 Thinking render ===


def test_thinking_collapsed_shows_header_and_preview() -> None:
    body = "\n".join(f"line {i}" for i in range(20))
    out = _render(render_thinking(body, tokens=123, elapsed=0.8, collapsed=True))
    assert "[thinking: 123 tokens, 0.8s]" in out
    assert "Ctrl+O" in out


def test_thinking_expanded_shows_full_body() -> None:
    body = "\n".join(f"line {i}" for i in range(20))
    out = _render(render_thinking(body, collapsed=False))
    assert "line 19" in out


def test_thinking_short_body_skips_marker() -> None:
    out = _render(render_thinking("just one line", collapsed=True))
    assert "Ctrl+O" not in out


# === C4 Markdown render ===


def test_markdown_render_returns_markdown_instance() -> None:
    md = render_markdown("# Hello\n\nThis is `code`.")
    # Rich's Markdown instance has a .markup attribute.
    assert md is not None


# === C5 Compact summary ===


def test_compact_summary_shows_stats() -> None:
    panel = render_compact_summary(
        before_tokens=10000, after_tokens=3000, tokens_saved=7000
    )
    out = _render(panel)
    assert "10000" in out
    assert "3000" in out
    assert "7000" in out
    assert "70%" in out
