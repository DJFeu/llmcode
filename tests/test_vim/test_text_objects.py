"""Tests for vim text object selectors."""
from __future__ import annotations

from llm_code.vim.types import VimMode, VimState, Register
from llm_code.vim.text_objects import select_text_object


def _state(buf: str, cursor: int) -> VimState:
    return VimState(
        buffer=buf, cursor=cursor, mode=VimMode.NORMAL,
        register=Register(),
    )


class TestWordObjects:
    def test_iw_selects_inner_word(self):
        start, end = select_text_object(_state("hello world", 2), "iw")
        assert start == 0
        assert end == 5  # exclusive end

    def test_aw_selects_word_with_trailing_space(self):
        start, end = select_text_object(_state("hello world", 2), "aw")
        assert start == 0
        assert end == 6  # includes trailing space

    def test_iW_selects_inner_WORD(self):
        start, end = select_text_object(_state("hello-world foo", 3), "iW")
        assert start == 0
        assert end == 11

    def test_aW_selects_WORD_with_trailing_space(self):
        start, end = select_text_object(_state("hello-world foo", 3), "aW")
        assert start == 0
        assert end == 12


class TestQuotedObjects:
    def test_inner_double_quote(self):
        start, end = select_text_object(_state('say "hello" now', 6), 'i"')
        assert start == 5
        assert end == 10

    def test_around_double_quote(self):
        start, end = select_text_object(_state('say "hello" now', 6), 'a"')
        assert start == 4
        assert end == 11

    def test_inner_single_quote(self):
        start, end = select_text_object(_state("say 'hello' now", 6), "i'")
        assert start == 5
        assert end == 10

    def test_around_single_quote(self):
        start, end = select_text_object(_state("say 'hello' now", 6), "a'")
        assert start == 4
        assert end == 11


class TestBracketObjects:
    def test_inner_paren(self):
        start, end = select_text_object(_state("fn(arg1, arg2)", 5), "i(")
        assert start == 3
        assert end == 13

    def test_around_paren(self):
        start, end = select_text_object(_state("fn(arg1, arg2)", 5), "a(")
        assert start == 2
        assert end == 14

    def test_inner_bracket(self):
        start, end = select_text_object(_state("a[1, 2]b", 3), "i[")
        assert start == 2
        assert end == 6

    def test_inner_brace(self):
        start, end = select_text_object(_state("{key: val}", 5), "i{")
        assert start == 1
        assert end == 9

    def test_inner_angle(self):
        start, end = select_text_object(_state("<div>hi</div>", 6), "i<")
        assert start == 5
        assert end == 7

    def test_returns_none_if_no_match(self):
        result = select_text_object(_state("hello", 2), "i(")
        assert result is None
