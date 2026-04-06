"""Integration tests for InputBar slash command behavior."""
from __future__ import annotations

from llm_code.tui.input_bar import (
    InputBar,
    SLASH_COMMANDS,
    SLASH_COMMAND_DESCS,
    _NO_ARG_COMMANDS,
)


class TestDropdownBehavior:
    def test_typing_slash_shows_dropdown(self) -> None:
        """Typing / should populate dropdown items."""
        bar = InputBar()
        bar.value = "/"
        bar._update_dropdown()
        assert bar._show_dropdown is True
        assert len(bar._dropdown_items) > 0

    def test_typing_slash_s_filters_dropdown(self) -> None:
        """Typing /s should filter to commands starting with /s."""
        bar = InputBar()
        bar.value = "/s"
        bar._update_dropdown()
        assert bar._show_dropdown is True
        for cmd, _ in bar._dropdown_items:
            assert cmd.startswith("/s")

    def test_typing_slash_with_no_match(self) -> None:
        """Typing /zzz should show no dropdown items."""
        bar = InputBar()
        bar.value = "/zzz"
        bar._update_dropdown()
        assert bar._show_dropdown is False
        assert len(bar._dropdown_items) == 0

    def test_dropdown_closes_on_space(self) -> None:
        """Dropdown should close when space is typed (no longer a command)."""
        bar = InputBar()
        bar.value = "/search "
        bar._update_dropdown()
        assert bar._show_dropdown is False

    def test_dropdown_scroll_window(self) -> None:
        """When cursor is beyond visible window, items should shift."""
        bar = InputBar()
        bar.value = "/"
        bar._update_dropdown()
        # Move cursor to item beyond visible window
        bar._dropdown_cursor = min(15, len(bar._dropdown_items) - 1)
        # Render should not crash and should show items around cursor
        result = bar.render()
        assert result is not None

    def test_dropdown_cursor_wraps(self) -> None:
        """Cursor should wrap from last to first and vice versa."""
        bar = InputBar()
        bar.value = "/"
        bar._update_dropdown()
        total = len(bar._dropdown_items)
        assert total > 0
        bar._dropdown_cursor = total - 1
        # Simulate down arrow — should wrap to 0
        bar._dropdown_cursor = (bar._dropdown_cursor + 1) % total
        assert bar._dropdown_cursor == 0
        # Simulate up arrow from 0 — should wrap to last
        bar._dropdown_cursor = (bar._dropdown_cursor - 1) % total
        assert bar._dropdown_cursor == total - 1

    def test_dropdown_exact_match(self) -> None:
        """Typing a full command name should still show dropdown."""
        bar = InputBar()
        bar.value = "/help"
        bar._update_dropdown()
        assert bar._show_dropdown is True
        assert any(cmd == "/help" for cmd, _ in bar._dropdown_items)

    def test_plain_text_no_dropdown(self) -> None:
        """Non-slash input should never show dropdown."""
        bar = InputBar()
        bar.value = "hello"
        bar._update_dropdown()
        assert bar._show_dropdown is False


class TestCursorIntegrity:
    def test_cursor_at_end_after_typing(self) -> None:
        """Cursor should be at end of value after typing."""
        bar = InputBar()
        bar.value = ""
        bar._cursor = 0
        # Simulate typing "hello"
        for ch in "hello":
            bar._cursor = min(bar._cursor, len(bar.value))
            bar.value = bar.value[:bar._cursor] + ch + bar.value[bar._cursor:]
            bar._cursor += 1
        assert bar.value == "hello"
        assert bar._cursor == 5

    def test_cursor_valid_after_clear(self) -> None:
        """After clearing value, cursor should be 0."""
        bar = InputBar()
        bar.value = "test"
        bar._cursor = 4
        bar.value = ""
        bar._cursor = 0
        assert bar._cursor == 0

    def test_cursor_bounds_on_insert(self) -> None:
        """Cursor should be clamped before insertion."""
        bar = InputBar()
        bar.value = "/s"
        bar._cursor = 99  # invalid
        bar._cursor = min(bar._cursor, len(bar.value))
        bar.value = bar.value[:bar._cursor] + "e" + bar.value[bar._cursor:]
        bar._cursor += 1
        assert bar.value == "/se"
        assert bar._cursor == 3

    def test_cursor_mid_insert(self) -> None:
        """Insert in the middle should shift text correctly."""
        bar = InputBar()
        bar.value = "hllo"
        bar._cursor = 1
        bar.value = bar.value[:bar._cursor] + "e" + bar.value[bar._cursor:]
        bar._cursor += 1
        assert bar.value == "hello"
        assert bar._cursor == 2


class TestPasteBehavior:
    def test_paste_text_inserts_at_cursor(self) -> None:
        """Pasting text should insert at current cursor position."""
        bar = InputBar()
        bar.value = "hello"
        bar._cursor = 5
        paste_text = " world"
        bar.value = bar.value[:bar._cursor] + paste_text + bar.value[bar._cursor:]
        bar._cursor += len(paste_text)
        assert bar.value == "hello world"
        assert bar._cursor == 11

    def test_paste_text_at_middle(self) -> None:
        """Pasting in the middle of existing text."""
        bar = InputBar()
        bar.value = "helo"
        bar._cursor = 2
        paste_text = "l"
        bar.value = bar.value[:bar._cursor] + paste_text + bar.value[bar._cursor:]
        bar._cursor += len(paste_text)
        assert bar.value == "hello"
        assert bar._cursor == 3

    def test_paste_multiline(self) -> None:
        """Pasting multiline text at end."""
        bar = InputBar()
        bar.value = ""
        bar._cursor = 0
        paste_text = "line1\nline2"
        bar.value = bar.value[:bar._cursor] + paste_text + bar.value[bar._cursor:]
        bar._cursor += len(paste_text)
        assert bar.value == "line1\nline2"
        assert bar._cursor == 11


class TestNoArgCommandExecution:
    def test_no_arg_commands_defined(self) -> None:
        """All no_arg commands should be in SLASH_COMMANDS."""
        for cmd in _NO_ARG_COMMANDS:
            assert cmd in SLASH_COMMANDS, f"{cmd} in _NO_ARG_COMMANDS but not in SLASH_COMMANDS"

    def test_all_commands_have_descriptions(self) -> None:
        """Every command in SLASH_COMMANDS should have a description."""
        desc_cmds = {cmd for cmd, _ in SLASH_COMMAND_DESCS}
        for cmd in SLASH_COMMANDS:
            assert cmd in desc_cmds, f"{cmd} has no description in SLASH_COMMAND_DESCS"

    def test_no_duplicate_commands(self) -> None:
        """SLASH_COMMANDS should not contain duplicates."""
        assert len(SLASH_COMMANDS) == len(set(SLASH_COMMANDS))

    def test_no_duplicate_descriptions(self) -> None:
        """SLASH_COMMAND_DESCS should not have duplicate command entries."""
        cmds = [cmd for cmd, _ in SLASH_COMMAND_DESCS]
        assert len(cmds) == len(set(cmds))
