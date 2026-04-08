"""Tier 2/3 wiring tests — connect widgets to the running TUI app.

These tests exercise the handler methods on LLMCodeTUI directly using mocks,
avoiding the cost of a full Textual runtime mount.
"""
from __future__ import annotations

from unittest.mock import MagicMock


from llm_code.cli.commands import COMMAND_REGISTRY, KNOWN_COMMANDS
from llm_code.tui.app import LLMCodeTUI
from llm_code.tui.chat_widgets import RateLimitBar, ToolBlock, ToolBlockData
from llm_code.tui.input_bar import InputBar
from llm_code.tui.status_bar import StatusBar


# ──────────────────────── Task 1: Ctrl+P Quick Open ────────────────────────


def test_insert_text_helper_routes_to_input_bar():
    """_insert_text_into_input should call InputBar.insert_text."""
    app = LLMCodeTUI.__new__(LLMCodeTUI)
    fake_input = MagicMock(spec=InputBar)
    app.query_one = MagicMock(return_value=fake_input)
    app._insert_text_into_input("llm_code/tui/app.py")
    fake_input.insert_text.assert_called_once_with("llm_code/tui/app.py")


def test_input_bar_insert_text_at_cursor():
    """InputBar.insert_text inserts at current cursor position and advances cursor."""
    # Use a minimal stand-in since Textual reactives require a mounted app
    class _Stand:
        value = "hello "
        _cursor = 6
    s = _Stand()
    InputBar.insert_text(s, "world")
    assert s.value == "hello world"
    assert s._cursor == 11


# ──────────────────────── Task 2: /settings command ────────────────────────


def test_settings_command_registered():
    """`settings` command is in the registry as a no_arg command."""
    assert "settings" in KNOWN_COMMANDS
    cmd = next(c for c in COMMAND_REGISTRY if c.name == "settings")
    assert cmd.no_arg is True


def test_settings_handler_exists():
    """LLMCodeTUI has a _cmd_settings handler."""
    assert callable(getattr(LLMCodeTUI, "_cmd_settings", None))


# ──────────────────────── Task 3: RateLimitBar wiring ────────────────────────


def test_rate_limit_bar_hidden_when_no_info():
    """_refresh_rate_limit_bar hides bar when cost_tracker.rate_limit_info is None."""
    app = LLMCodeTUI.__new__(LLMCodeTUI)
    tracker = MagicMock()
    tracker.rate_limit_info = None
    app._cost_tracker = tracker
    bar = MagicMock(spec=RateLimitBar)
    app.query_one = MagicMock(return_value=bar)
    app._refresh_rate_limit_bar()
    assert bar.display is False


def test_rate_limit_bar_visible_when_info_present():
    """_refresh_rate_limit_bar shows bar when info is present."""
    app = LLMCodeTUI.__new__(LLMCodeTUI)
    tracker = MagicMock()
    tracker.rate_limit_info = {"used": 500, "limit": 1000, "reset_at": 0}
    app._cost_tracker = tracker
    bar = MagicMock(spec=RateLimitBar)
    app.query_one = MagicMock(return_value=bar)
    app._refresh_rate_limit_bar()
    assert bar.display is True


# ──────────────────────── Task 4: Status bar reactive feed ────────────────────────


def test_status_bar_reactive_fields_have_watchers():
    """StatusBar exposes all the Tier-2 reactive fields required by the runtime feed."""
    for field in (
        "model", "turn_count", "cwd_basename", "git_branch",
        "permission_mode", "cost", "tokens", "context_used",
    ):
        assert hasattr(StatusBar, field), f"StatusBar missing reactive field: {field}"


# ──────────────────────── Task 5: Ctrl+V verbose toggle ────────────────────────


def _make_tool_block(is_error: bool) -> ToolBlock:
    return ToolBlock(
        ToolBlockData(
            tool_name="bash",
            args_display="ls",
            result="boom" if is_error else "ok",
            is_error=is_error,
        )
    )


def test_toggle_verbose_on_error_block():
    """_toggle_last_error_verbose toggles verbose when an error block exists."""
    app = LLMCodeTUI.__new__(LLMCodeTUI)
    err_block = _make_tool_block(is_error=True)
    ok_block = _make_tool_block(is_error=False)
    fake_chat = MagicMock()
    fake_chat.query = MagicMock(return_value=[ok_block, err_block])
    app.query_one = MagicMock(return_value=fake_chat)
    # Avoid calling real Textual refresh
    err_block.refresh = MagicMock()  # type: ignore[method-assign]
    assert err_block._verbose is False
    toggled = app._toggle_last_error_verbose()
    assert toggled is True
    assert err_block._verbose is True


def test_toggle_verbose_noop_without_error_block():
    """_toggle_last_error_verbose is a no-op when only non-error blocks exist."""
    app = LLMCodeTUI.__new__(LLMCodeTUI)
    ok_block = _make_tool_block(is_error=False)
    fake_chat = MagicMock()
    fake_chat.query = MagicMock(return_value=[ok_block])
    app.query_one = MagicMock(return_value=fake_chat)
    toggled = app._toggle_last_error_verbose()
    assert toggled is False
    assert ok_block._verbose is False
