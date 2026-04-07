"""Tests for the hardened inline markdown renderer."""
from __future__ import annotations

from llm_code.tui.chat_view import _styled_text


def test_bold_rendered():
    t = _styled_text("hello **world**")
    assert "world" in t.plain


def test_code_rendered():
    t = _styled_text("call `foo()` here")
    assert "foo()" in t.plain


def test_strikethrough_rendered_as_plain():
    t = _styled_text("this is ~~old~~ text")
    # Content present, strike not rendered as a special style
    assert "old" in t.plain


def test_markdown_link():
    t = _styled_text("see [docs](https://example.com)")
    assert "docs" in t.plain
    # No raw URL leaked into label area
    assert "(https://example.com)" not in t.plain


def test_bare_url_styled():
    t = _styled_text("visit https://example.com now")
    assert "https://example.com" in t.plain


def test_blockquote_indented():
    t = _styled_text("> quoted line\nplain")
    assert "quoted line" in t.plain
    assert "plain" in t.plain


def test_fenced_code_block_with_lang():
    src = "before\n```python\nprint('hi')\n```\nafter"
    t = _styled_text(src)
    assert "print('hi')" in t.plain
    assert "python" in t.plain
    assert "before" in t.plain
    assert "after" in t.plain


def test_heading_rendered():
    t = _styled_text("# Title here")
    assert "Title here" in t.plain
