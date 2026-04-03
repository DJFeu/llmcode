"""Tests for vim type definitions."""
from __future__ import annotations

from llm_code.vim.types import (
    VimMode,
    Register,
    ParsedCommand,
    initial_state,
)


class TestVimMode:
    def test_normal_mode_exists(self):
        assert VimMode.NORMAL.value == "normal"

    def test_insert_mode_exists(self):
        assert VimMode.INSERT.value == "insert"


class TestVimState:
    def test_initial_state_is_insert(self):
        state = initial_state("hello world")
        assert state.mode == VimMode.INSERT
        assert state.buffer == "hello world"
        assert state.cursor == 11  # end of buffer
        assert state.register == Register(content="")

    def test_state_is_frozen(self):
        state = initial_state("")
        try:
            state.mode = VimMode.NORMAL  # type: ignore
            assert False, "Should raise"
        except (AttributeError, TypeError):
            pass

    def test_with_cursor(self):
        state = initial_state("hello")
        new_state = state.with_cursor(2)
        assert new_state.cursor == 2
        assert new_state.buffer == "hello"
        assert state.cursor == 5  # original unchanged

    def test_with_buffer(self):
        state = initial_state("hello")
        new_state = state.with_buffer("world", cursor=3)
        assert new_state.buffer == "world"
        assert new_state.cursor == 3

    def test_with_mode(self):
        state = initial_state("hello")
        new_state = state.with_mode(VimMode.NORMAL)
        assert new_state.mode == VimMode.NORMAL
        assert state.mode == VimMode.INSERT

    def test_cursor_clamped_to_buffer_length(self):
        state = initial_state("hi")
        new_state = state.with_cursor(100)
        assert new_state.cursor == 2


class TestRegister:
    def test_empty_register(self):
        reg = Register(content="")
        assert reg.content == ""

    def test_register_with_content(self):
        reg = Register(content="yanked text")
        assert reg.content == "yanked text"


class TestParsedCommand:
    def test_motion_only(self):
        cmd = ParsedCommand(count=1, operator=None, motion="w", text_object=None)
        assert cmd.count == 1
        assert cmd.operator is None
        assert cmd.motion == "w"

    def test_operator_with_motion(self):
        cmd = ParsedCommand(count=2, operator="d", motion="w", text_object=None)
        assert cmd.count == 2
        assert cmd.operator == "d"

    def test_operator_with_text_object(self):
        cmd = ParsedCommand(count=1, operator="d", motion=None, text_object="iw")
        assert cmd.text_object == "iw"
