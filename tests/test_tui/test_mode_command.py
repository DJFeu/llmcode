"""Tests for /mode slash command (suggest/normal/plan)."""
from __future__ import annotations

from unittest.mock import MagicMock


from llm_code.runtime.permissions import PermissionMode, PermissionPolicy
from llm_code.tui.status_bar import StatusBar


class TestModeStatusBar:
    """Verify status bar displays mode labels correctly."""

    def test_suggest_shown_in_status_bar(self) -> None:
        bar = StatusBar()
        bar.plan_mode = "SUGGEST"
        bar.model = "qwen3.5"
        content = bar._format_content()
        assert "SUGGEST" in content

    def test_suggest_before_model(self) -> None:
        bar = StatusBar()
        bar.plan_mode = "SUGGEST"
        bar.model = "qwen3.5"
        content = bar._format_content()
        assert content.index("SUGGEST") < content.index("qwen3.5")

    def test_normal_hides_mode_label(self) -> None:
        bar = StatusBar()
        bar.plan_mode = ""
        bar.model = "qwen3.5"
        content = bar._format_content()
        assert "SUGGEST" not in content
        assert "PLAN" not in content


class TestCmdMode:
    """Test _cmd_mode behavior using a minimal mock app."""

    def _make_app(self) -> MagicMock:
        """Create a mock app with necessary attributes for _cmd_mode."""
        from llm_code.tui.app import LLMCodeTUI

        # We import the method and bind it to a mock to avoid full Textual init
        app = MagicMock()
        app._plan_mode = False
        app._initial_mode = None

        # Create real StatusBar and mock ChatScrollView
        status = StatusBar()
        status.plan_mode = ""
        chat = MagicMock()

        def query_one(cls):
            if cls is StatusBar or (isinstance(cls, str) and cls == "StatusBar"):
                return status
            return chat

        # Make query_one work with both class and string
        from llm_code.tui.chat_view import ChatScrollView
        _map = {StatusBar: status, ChatScrollView: chat}

        def _query_one(key):
            return _map.get(key, chat)

        app.query_one = _query_one
        app._runtime = MagicMock()
        app._runtime._permissions = PermissionPolicy(mode=PermissionMode.WORKSPACE_WRITE)

        # Bind the real _cmd_mode method
        app._cmd_mode = LLMCodeTUI._cmd_mode.__get__(app, type(app))

        return app, status, chat

    def test_mode_no_args_shows_current(self) -> None:
        app, status, chat = self._make_app()
        app._cmd_mode("")
        chat.add_entry.assert_called_once()
        msg = chat.add_entry.call_args[0][0]._text
        assert "Current mode: normal" in msg
        assert "suggest" in msg
        assert "plan" in msg

    def test_mode_suggest_switches_to_prompt(self) -> None:
        app, status, chat = self._make_app()
        app._cmd_mode("suggest")
        assert app._runtime._permissions._mode == PermissionMode.PROMPT
        assert status.plan_mode == "SUGGEST"
        assert not app._plan_mode
        chat.add_entry.assert_called_once()

    def test_mode_normal_switches_to_workspace_write(self) -> None:
        app, status, chat = self._make_app()
        # Start from suggest mode
        app._plan_mode = False
        status.plan_mode = "SUGGEST"
        app._runtime._permissions._mode = PermissionMode.PROMPT

        app._cmd_mode("normal")
        assert app._runtime._permissions._mode == PermissionMode.WORKSPACE_WRITE
        assert status.plan_mode == ""
        assert not app._plan_mode

    def test_mode_plan_switches_to_plan(self) -> None:
        app, status, chat = self._make_app()
        app._cmd_mode("plan")
        assert app._runtime._permissions._mode == PermissionMode.PLAN
        assert status.plan_mode == "PLAN"
        assert app._plan_mode

    def test_mode_invalid_shows_error(self) -> None:
        app, status, chat = self._make_app()
        app._cmd_mode("turbo")
        chat.add_entry.assert_called_once()
        msg = chat.add_entry.call_args[0][0]._text
        assert "Unknown mode: turbo" in msg

    def test_mode_show_current_when_in_plan(self) -> None:
        app, status, chat = self._make_app()
        app._plan_mode = True
        status.plan_mode = "PLAN"
        app._cmd_mode("")
        msg = chat.add_entry.call_args[0][0]._text
        assert "Current mode: plan" in msg

    def test_mode_show_current_when_in_suggest(self) -> None:
        app, status, chat = self._make_app()
        app._plan_mode = False
        status.plan_mode = "SUGGEST"
        app._cmd_mode("")
        msg = chat.add_entry.call_args[0][0]._text
        assert "Current mode: suggest" in msg
