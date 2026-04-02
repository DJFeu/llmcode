"""Tests for IncrementalMarkdownRenderer (streaming markdown)."""
from __future__ import annotations

import io

from rich.console import Console

from llm_code.cli.streaming import IncrementalMarkdownRenderer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_renderer() -> tuple[IncrementalMarkdownRenderer, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, highlight=False, markup=False, no_color=True)
    renderer = IncrementalMarkdownRenderer(console)
    return renderer, buf


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestIncrementalMarkdownRenderer:
    def test_paragraph_flushed_on_double_newline(self) -> None:
        renderer, buf = _make_renderer()
        renderer.feed("Hello world.\n\n")
        output = buf.getvalue()
        assert "Hello world." in output

    def test_incomplete_paragraph_not_flushed_early(self) -> None:
        renderer, buf = _make_renderer()
        renderer.feed("Still typing")
        output = buf.getvalue()
        # Not flushed yet — nothing rendered
        assert output == ""

    def test_finish_flushes_remaining_content(self) -> None:
        renderer, buf = _make_renderer()
        renderer.feed("Remaining text")
        renderer.finish()
        output = buf.getvalue()
        assert "Remaining text" in output

    def test_code_block_flushed_on_closing_backticks(self) -> None:
        renderer, buf = _make_renderer()
        renderer.feed("```python\nprint('hi')\n```\n\n")
        output = buf.getvalue()
        assert "print" in output

    def test_code_block_not_flushed_before_closing(self) -> None:
        renderer, buf = _make_renderer()
        renderer.feed("```python\nprint('hi')")
        output = buf.getvalue()
        # Code block not yet closed — nothing rendered
        assert output == ""

    def test_heading_flushed_on_double_newline(self) -> None:
        renderer, buf = _make_renderer()
        renderer.feed("# My Heading\n\n")
        output = buf.getvalue()
        assert "My Heading" in output

    def test_token_by_token_streaming(self) -> None:
        renderer, buf = _make_renderer()
        tokens = list("Hello world.\n\n")
        for tok in tokens:
            renderer.feed(tok)
        output = buf.getvalue()
        assert "Hello world." in output

    def test_multiple_paragraphs(self) -> None:
        renderer, buf = _make_renderer()
        renderer.feed("First paragraph.\n\nSecond paragraph.\n\n")
        output = buf.getvalue()
        assert "First paragraph." in output
        assert "Second paragraph." in output

    def test_multiple_code_blocks(self) -> None:
        renderer, buf = _make_renderer()
        renderer.feed("```python\nfoo = 1\n```\n\n```bash\necho hi\n```\n\n")
        output = buf.getvalue()
        assert "foo" in output
        assert "echo" in output

    def test_empty_input_no_output(self) -> None:
        renderer, buf = _make_renderer()
        renderer.finish()
        output = buf.getvalue()
        assert output == ""

    def test_list_items_flushed(self) -> None:
        renderer, buf = _make_renderer()
        renderer.feed("- item one\n- item two\n\n")
        output = buf.getvalue()
        assert "item one" in output
        assert "item two" in output

    def test_finish_on_code_block_without_closing(self) -> None:
        """finish() should flush partial code block."""
        renderer, buf = _make_renderer()
        renderer.feed("```python\npartial code")
        renderer.finish()
        output = buf.getvalue()
        assert "partial code" in output

    def test_text_and_code_block_together(self) -> None:
        renderer, buf = _make_renderer()
        renderer.feed("Some text.\n\n```python\nx = 42\n```\n\n")
        output = buf.getvalue()
        assert "Some text." in output
        assert "x = 42" in output
