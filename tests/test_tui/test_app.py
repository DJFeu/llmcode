"""Integration test for the fullscreen TUI app."""
from __future__ import annotations

import pytest
from llm_code.tui.app import LLMCodeTUI
from llm_code.tui.header_bar import HeaderBar
from llm_code.tui.chat_view import ChatScrollView, AssistantText
from llm_code.tui.input_bar import InputBar
from llm_code.tui.status_bar import StatusBar


class TestAppCreation:
    def test_app_creates(self):
        app = LLMCodeTUI()
        assert app is not None
        assert app.title == "llm-code"

    def test_app_has_required_widgets(self):
        app = LLMCodeTUI()
        # Verify compose yields expected widget types
        from llm_code.tui.header_bar import HeaderBar
        from llm_code.tui.chat_view import ChatScrollView
        from llm_code.tui.input_bar import InputBar
        from llm_code.tui.status_bar import StatusBar
        widgets = list(app.compose())
        type_names = [type(w).__name__ for w in widgets]
        assert "HeaderBar" in type_names
        assert "ChatScrollView" in type_names
        assert "InputBar" in type_names
        assert "StatusBar" in type_names


class TestEntryPointFlags:
    def test_tui_main_has_ink_flag(self):
        """Verify tui_main accepts --ink flag."""
        from click.testing import CliRunner
        from llm_code.cli.tui_main import main
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "--ink" in result.output
        assert "--lite" in result.output


@pytest.mark.asyncio
async def test_app_boots_and_accepts_input():
    """Integration test: app boots into fullscreen with all 4 widget areas."""
    app = LLMCodeTUI()
    async with app.run_test() as pilot:
        # Verify all widgets present
        header = app.query_one(HeaderBar)
        assert header is not None

        chat = app.query_one(ChatScrollView)
        assert chat is not None

        input_bar = app.query_one(InputBar)
        assert input_bar is not None

        status = app.query_one(StatusBar)
        assert status is not None

        # Verify title
        assert app.title == "llm-code"


@pytest.mark.asyncio
async def test_slash_help():
    """Integration test: /help command shows help text in chat."""
    app = LLMCodeTUI()
    async with app.run_test() as pilot:
        # Type /help and press enter
        input_bar = app.query_one(InputBar)
        input_bar.value = "/help"
        input_bar.post_message(InputBar.Submitted("/help"))
        await pilot.pause()

        # Verify help text appeared in chat
        chat = app.query_one(ChatScrollView)
        children = chat.query("AssistantText")
        assert len(children) > 0


@pytest.mark.asyncio
async def test_slash_clear():
    """Integration test: /clear removes all chat entries."""
    app = LLMCodeTUI()
    async with app.run_test() as pilot:
        # Add something to chat via /help first
        input_bar = app.query_one(InputBar)
        input_bar.post_message(InputBar.Submitted("/help"))
        await pilot.pause()

        chat = app.query_one(ChatScrollView)
        assert len(chat.children) > 0

        # Now clear
        input_bar.post_message(InputBar.Submitted("/clear"))
        await pilot.pause()

        assert len(chat.children) == 0


@pytest.mark.asyncio
async def test_slash_vim_toggle():
    """Integration test: /vim toggles vim mode on InputBar and StatusBar."""
    app = LLMCodeTUI()
    async with app.run_test() as pilot:
        input_bar = app.query_one(InputBar)
        status = app.query_one(StatusBar)

        # Initially no vim mode
        assert input_bar.vim_mode == ""
        assert status.vim_mode == ""

        # Enable vim
        input_bar.post_message(InputBar.Submitted("/vim"))
        await pilot.pause()

        assert input_bar.vim_mode == "NORMAL"
        assert status.vim_mode == "NORMAL"

        # Disable vim
        input_bar.post_message(InputBar.Submitted("/vim"))
        await pilot.pause()

        assert input_bar.vim_mode == ""
        assert status.vim_mode == ""
