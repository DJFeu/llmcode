"""Tests for M15 Group D tool event components (D1-D4)."""
from __future__ import annotations

from rich.console import Console

from llm_code.view.repl import style
from llm_code.view.repl.components.code_block import render_syntax
from llm_code.view.repl.components.file_link import render_path
from llm_code.view.repl.components.progress_line import (
    render_failure,
    render_start,
    render_success,
)
from llm_code.view.repl.components.structured_diff import (
    render_structured_diff,
)


def _render(renderable) -> str:
    console = Console(width=120, record=True, color_system="truecolor")
    console.print(renderable)
    return console.export_text()


# === D1 Progress line ===


def test_start_line_has_start_glyph_and_tool_name() -> None:
    out = _render(render_start("bash", {"cmd": "ls"}))
    assert "▶" in out
    assert "bash" in out
    assert "cmd=ls" in out


def test_success_line_has_success_glyph() -> None:
    out = _render(render_success("edit_file", "42 lines", elapsed=0.8))
    assert "✓" in out
    assert "edit_file" in out
    assert "42 lines" in out
    assert "0.8s" in out


def test_failure_line_has_failure_glyph() -> None:
    out = _render(render_failure("bash", "command not found", exit_code=127))
    assert "✗" in out
    assert "bash" in out
    assert "exit 127" in out
    assert "command not found" in out


def test_long_args_truncate() -> None:
    big_args = {"x": "a" * 100}
    out = _render(render_start("tool", big_args))
    assert "…" in out


# === D2 Structured diff ===


def test_structured_diff_parses_hunks_and_lines() -> None:
    diff = """--- foo.py
+++ foo.py
@@ -10,3 +10,4 @@
 context
-removed line
+added line
+another added
"""
    out = _render(render_structured_diff(diff))
    assert "removed line" in out
    assert "added line" in out
    assert "@@ -10,3 +10,4 @@" in out


def test_structured_diff_has_gutter_line_numbers() -> None:
    diff = """@@ -5,2 +5,2 @@
-old
+new
"""
    out = _render(render_structured_diff(diff))
    assert "5" in out


# === D3 Code block ===


def test_render_syntax_with_explicit_language() -> None:
    syntax = render_syntax("x = 1", language="python")
    # Rich exposes lexer as ``_lexer_name`` internally; verify the
    # Syntax object is constructed without raising.
    assert syntax is not None
    rendered = _render(syntax)
    assert "x = 1" in rendered


def test_render_syntax_autodetect_from_filename() -> None:
    syntax = render_syntax("console.log('hi')", filename="script.js")
    # Guess lexer may return 'javascript' or similar — just verify it
    # runs without raising.
    assert syntax is not None


# === D4 File link ===


def test_file_link_wraps_path_in_osc8() -> None:
    text = render_path("/tmp/foo.txt")
    # The Rich Text object carries the link in its spans.
    styles = [str(span.style) for span in text.spans]
    # Rich emits the OSC8 wrapping at render time; check the style
    # carries a "link" token.
    assert any("link" in s for s in styles + [str(text.style)])


def test_file_link_uses_palette_tone() -> None:
    text = render_path("example.py")
    assert style.palette.file_path_fg in str(text.style)
