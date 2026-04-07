"""Tests for the structured diff renderer."""
from __future__ import annotations

import pytest

from llm_code.tui.diff_render import render_diff, render_diff_lines


@pytest.mark.unit
class TestRenderDiff:
    def test_renders_simple_change(self) -> None:
        old = "line1\nline2\nline3\n"
        new = "line1\nLINE2\nline3\n"
        text = render_diff(old, new, "foo.txt")
        plain = text.plain
        assert "@@" in plain
        assert "-line2" in plain
        assert "+LINE2" in plain

    def test_hunk_header_styled_cyan(self) -> None:
        old = "a\nb\nc\n"
        new = "a\nB\nc\n"
        text = render_diff(old, new, "x")
        # Verify the @@ span carries the cyan style
        spans = [s for s in text.spans if "cyan" in str(s.style)]
        assert spans, f"expected a cyan span, got {text.spans}"

    def test_add_remove_styles_present(self) -> None:
        old = "a\n"
        new = "a\nb\n"
        text = render_diff(old, new, "x")
        styles = [str(s.style) for s in text.spans]
        assert any("green" in s for s in styles), styles
        # No removals in this test, so just confirm the green path works.

    def test_remove_style_present(self) -> None:
        old = "a\nb\n"
        new = "a\n"
        text = render_diff(old, new, "x")
        styles = [str(s.style) for s in text.spans]
        assert any("red" in s for s in styles), styles

    def test_truncation_footer(self) -> None:
        old = "\n".join(f"line{i}" for i in range(100)) + "\n"
        new = "\n".join(f"LINE{i}" for i in range(100)) + "\n"
        text = render_diff(old, new, "big.txt", max_lines=10)
        assert "more line" in text.plain
        assert "+" in text.plain

    def test_empty_diff_when_identical(self) -> None:
        text = render_diff("same\n", "same\n", "x")
        assert text.plain == ""

    def test_render_diff_lines_handles_bare_lines(self) -> None:
        # Pre-formatted diff_lines without hunk headers (legacy ToolBlock path)
        lines = ["+added", "-removed", " context"]
        text = render_diff_lines(lines, max_lines=10)
        assert "+added" in text.plain
        assert "-removed" in text.plain
        assert "context" in text.plain
