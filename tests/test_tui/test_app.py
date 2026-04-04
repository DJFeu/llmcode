"""Integration test for the fullscreen TUI app."""
from __future__ import annotations

import pytest
from llm_code.tui.app import LLMCodeTUI


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
