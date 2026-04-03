"""Tests for vim operator functions."""
from __future__ import annotations

from llm_code.vim.types import VimMode, VimState, Register
from llm_code.vim.operators import (
    op_delete,
    op_change,
    op_yank,
    op_delete_line,
    op_change_line,
    op_yank_line,
    op_x,
    op_replace,
    op_tilde,
    op_join,
    op_put_after,
    op_put_before,
    op_open_below,
    op_open_above,
    op_indent_right,
    op_indent_left,
)


def _state(buf: str, cursor: int, reg: str = "") -> VimState:
    return VimState(
        buffer=buf, cursor=cursor, mode=VimMode.NORMAL,
        register=Register(content=reg),
    )


class TestDeleteOperator:
    def test_delete_range(self):
        s = op_delete(_state("hello world", 0), 0, 6)
        assert s.buffer == "world"
        assert s.cursor == 0
        assert s.register.content == "hello "

    def test_delete_at_end(self):
        s = op_delete(_state("hello", 3), 3, 5)
        assert s.buffer == "hel"
        assert s.register.content == "lo"


class TestChangeOperator:
    def test_change_enters_insert_mode(self):
        s = op_change(_state("hello world", 0), 0, 5)
        assert s.buffer == " world"
        assert s.mode == VimMode.INSERT
        assert s.register.content == "hello"
        assert s.cursor == 0


class TestYankOperator:
    def test_yank_copies_to_register(self):
        s = op_yank(_state("hello world", 0), 0, 5)
        assert s.register.content == "hello"
        assert s.buffer == "hello world"  # unchanged
        assert s.cursor == 0


class TestLineVariants:
    def test_dd_deletes_entire_buffer(self):
        s = op_delete_line(_state("hello world", 5))
        assert s.buffer == ""
        assert s.register.content == "hello world"

    def test_cc_clears_and_enters_insert(self):
        s = op_change_line(_state("hello world", 5))
        assert s.buffer == ""
        assert s.mode == VimMode.INSERT

    def test_yy_yanks_entire_buffer(self):
        s = op_yank_line(_state("hello world", 5))
        assert s.register.content == "hello world"
        assert s.buffer == "hello world"


class TestMiscOperators:
    def test_x_deletes_char_under_cursor(self):
        s = op_x(_state("hello", 1))
        assert s.buffer == "hllo"
        assert s.register.content == "e"

    def test_x_at_end(self):
        s = op_x(_state("hello", 4))
        assert s.buffer == "hell"

    def test_replace_char(self):
        s = op_replace(_state("hello", 1), "a")
        assert s.buffer == "hallo"
        assert s.cursor == 1

    def test_tilde_toggles_case(self):
        s = op_tilde(_state("Hello", 0))
        assert s.buffer == "hello"
        assert s.cursor == 1

    def test_tilde_on_lowercase(self):
        s = op_tilde(_state("hello", 0))
        assert s.buffer == "Hello"

    def test_join_single_line_noop(self):
        s = op_join(_state("hello", 3))
        assert s.buffer == "hello"

    def test_put_after(self):
        s = op_put_after(_state("hello", 2, reg="XY"))
        assert s.buffer == "helXYlo"
        assert s.cursor == 4  # end of pasted text

    def test_put_before(self):
        s = op_put_before(_state("hello", 2, reg="XY"))
        assert s.buffer == "heXYllo"
        assert s.cursor == 3

    def test_open_below(self):
        s = op_open_below(_state("hello", 3))
        assert s.mode == VimMode.INSERT
        assert s.cursor == len(s.buffer)

    def test_open_above(self):
        s = op_open_above(_state("hello", 3))
        assert s.mode == VimMode.INSERT
        assert s.cursor == 0

    def test_indent_right(self):
        s = op_indent_right(_state("hello", 0))
        assert s.buffer == "  hello"

    def test_indent_left(self):
        s = op_indent_left(_state("  hello", 0))
        assert s.buffer == "hello"

    def test_indent_left_no_indent(self):
        s = op_indent_left(_state("hello", 0))
        assert s.buffer == "hello"
