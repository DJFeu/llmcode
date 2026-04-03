"""Tests for vim state machine transitions."""
from __future__ import annotations

from llm_code.vim.types import VimMode, VimState, Register, initial_state
from llm_code.vim.transitions import handle_key


def _normal(buf: str, cursor: int, reg: str = "") -> VimState:
    return VimState(
        buffer=buf, cursor=cursor, mode=VimMode.NORMAL,
        register=Register(content=reg),
    )


class TestModeTransitions:
    def test_esc_in_insert_switches_to_normal(self):
        state = initial_state("hello")
        result = handle_key(state, "\x1b")  # Escape
        assert result.mode == VimMode.NORMAL

    def test_i_in_normal_switches_to_insert(self):
        state = _normal("hello", 2)
        result = handle_key(state, "i")
        assert result.mode == VimMode.INSERT
        assert result.cursor == 2

    def test_a_in_normal_switches_to_insert_after(self):
        state = _normal("hello", 2)
        result = handle_key(state, "a")
        assert result.mode == VimMode.INSERT
        assert result.cursor == 3

    def test_A_in_normal_switches_to_insert_at_end(self):
        state = _normal("hello", 1)
        result = handle_key(state, "A")
        assert result.mode == VimMode.INSERT
        assert result.cursor == 5

    def test_I_in_normal_switches_to_insert_at_start(self):
        state = _normal("  hello", 4)
        result = handle_key(state, "I")
        assert result.mode == VimMode.INSERT
        assert result.cursor == 2  # first non-blank


class TestNormalMotions:
    def test_h_moves_left(self):
        state = _normal("hello", 3)
        result = handle_key(state, "h")
        assert result.cursor == 2

    def test_l_moves_right(self):
        state = _normal("hello", 1)
        result = handle_key(state, "l")
        assert result.cursor == 2

    def test_w_moves_word(self):
        state = _normal("hello world", 0)
        result = handle_key(state, "w")
        assert result.cursor == 6

    def test_0_goes_to_start(self):
        state = _normal("hello", 3)
        result = handle_key(state, "0")
        assert result.cursor == 0

    def test_dollar_goes_to_end(self):
        state = _normal("hello", 1)
        result = handle_key(state, "$")
        assert result.cursor == 4


class TestNormalOperators:
    def test_x_deletes_char(self):
        state = _normal("hello", 1)
        result = handle_key(state, "x")
        assert result.buffer == "hllo"

    def test_dd_deletes_line(self):
        state = _normal("hello", 2)
        s1 = handle_key(state, "d")
        result = handle_key(s1, "d")
        assert result.buffer == ""
        assert result.register.content == "hello"

    def test_p_puts_after(self):
        state = _normal("hllo", 0, reg="e")
        result = handle_key(state, "p")
        assert result.buffer == "hello"

    def test_u_undoes_last(self):
        state = _normal("hello", 1)
        after_x = handle_key(state, "x")
        assert after_x.buffer == "hllo"
        undone = handle_key(after_x, "u")
        assert undone.buffer == "hello"


class TestInsertMode:
    def test_typing_inserts_char(self):
        state = VimState(
            buffer="hllo", cursor=1, mode=VimMode.INSERT,
            register=Register(),
        )
        result = handle_key(state, "e")
        assert result.buffer == "hello"
        assert result.cursor == 2

    def test_backspace_in_insert(self):
        state = VimState(
            buffer="hello", cursor=3, mode=VimMode.INSERT,
            register=Register(),
        )
        result = handle_key(state, "\x7f")  # backspace
        assert result.buffer == "helo"
        assert result.cursor == 2


class TestCountPrefix:
    def test_3h_moves_left_3(self):
        state = _normal("hello world", 6)
        s1 = handle_key(state, "3")
        result = handle_key(s1, "h")
        assert result.cursor == 3


class TestDotRepeat:
    def test_dot_repeats_last_command(self):
        state = _normal("hello", 0)
        after_x = handle_key(state, "x")
        assert after_x.buffer == "ello"
        repeated = handle_key(after_x, ".")
        assert repeated.buffer == "llo"
