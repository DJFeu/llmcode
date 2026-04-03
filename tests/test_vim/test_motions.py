"""Tests for vim motion functions."""
from __future__ import annotations

from llm_code.vim.types import VimMode, VimState, Register
from llm_code.vim.motions import (
    move_h, move_l,
    move_w, move_b, move_e,
    move_W, move_B, move_E,
    move_0, move_caret, move_dollar,
    move_gg, move_G,
    move_f, move_F, move_t, move_T,
)


def _state(buf: str, cursor: int) -> VimState:
    return VimState(
        buffer=buf, cursor=cursor, mode=VimMode.NORMAL,
        register=Register(),
    )


class TestCharMotions:
    def test_h_moves_left(self):
        assert move_h(_state("hello", 3), 1) == 2

    def test_h_stops_at_zero(self):
        assert move_h(_state("hello", 0), 1) == 0

    def test_h_count(self):
        assert move_h(_state("hello", 4), 3) == 1

    def test_l_moves_right(self):
        assert move_l(_state("hello", 1), 1) == 2

    def test_l_stops_at_end(self):
        assert move_l(_state("hello", 4), 1) == 4  # NORMAL stops at len-1

    def test_l_count(self):
        assert move_l(_state("hello", 0), 3) == 3


class TestWordMotions:
    def test_w_moves_to_next_word(self):
        assert move_w(_state("hello world", 0), 1) == 6

    def test_w_from_middle_of_word(self):
        assert move_w(_state("hello world", 2), 1) == 6

    def test_w_at_end(self):
        assert move_w(_state("hello", 4), 1) == 4

    def test_b_moves_to_prev_word_start(self):
        assert move_b(_state("hello world", 8), 1) == 6

    def test_b_from_word_start(self):
        assert move_b(_state("hello world", 6), 1) == 0

    def test_e_moves_to_word_end(self):
        assert move_e(_state("hello world", 0), 1) == 4

    def test_e_from_word_end(self):
        assert move_e(_state("hello world", 4), 1) == 10

    def test_W_skips_punctuation(self):
        assert move_W(_state("hello-world foo", 0), 1) == 12

    def test_B_skips_punctuation(self):
        assert move_B(_state("foo hello-world", 14), 1) == 4

    def test_E_skips_punctuation(self):
        assert move_E(_state("hello-world foo", 0), 1) == 10


class TestLineMotions:
    def test_0_goes_to_line_start(self):
        assert move_0(_state("  hello", 5)) == 0

    def test_caret_goes_to_first_nonblank(self):
        assert move_caret(_state("  hello", 5)) == 2

    def test_caret_on_no_indent(self):
        assert move_caret(_state("hello", 3)) == 0

    def test_dollar_goes_to_line_end(self):
        assert move_dollar(_state("hello", 1)) == 4


class TestDocMotions:
    def test_gg_goes_to_start(self):
        assert move_gg(_state("hello\nworld", 8)) == 0

    def test_G_goes_to_end(self):
        assert move_G(_state("hello\nworld", 0)) == 10


class TestCharSearch:
    def test_f_finds_char_forward(self):
        assert move_f(_state("hello world", 0), "w") == 6

    def test_f_returns_current_if_not_found(self):
        assert move_f(_state("hello", 0), "z") == 0

    def test_F_finds_char_backward(self):
        assert move_F(_state("hello world", 10), "o") == 7

    def test_t_stops_before_char(self):
        assert move_t(_state("hello world", 0), "w") == 5

    def test_T_stops_after_char(self):
        assert move_T(_state("hello world", 10), "o") == 8
