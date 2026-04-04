"""Tests for TUI widgets."""
from __future__ import annotations

from llm_code.tui.header_bar import HeaderBar
from llm_code.tui.status_bar import StatusBar


class TestHeaderBar:
    def test_creates_with_defaults(self):
        bar = HeaderBar()
        assert bar.model == ""
        assert bar.project == ""
        assert bar.branch == ""

    def test_format_content(self):
        bar = HeaderBar()
        bar.model = "qwen3.5"
        bar.project = "my-project"
        bar.branch = "main"
        content = bar._format_content()
        assert "qwen3.5" in content
        assert "my-project" in content
        assert "main" in content

    def test_format_content_with_only_model(self):
        bar = HeaderBar()
        bar.model = "gpt-4"
        content = bar._format_content()
        assert content == "llm-code · gpt-4"

    def test_format_content_with_empty_strings(self):
        bar = HeaderBar()
        content = bar._format_content()
        assert content == "llm-code"

    def test_format_content_partial(self):
        bar = HeaderBar()
        bar.model = "claude"
        bar.branch = "dev"
        content = bar._format_content()
        assert content == "llm-code · claude · dev"


class TestStatusBar:
    def test_creates_with_defaults(self):
        bar = StatusBar()
        assert bar.tokens == 0
        assert bar.cost == ""
        assert bar.is_streaming is False
        assert bar.vim_mode == ""

    def test_format_content(self):
        bar = StatusBar()
        bar.model = "qwen3.5"
        bar.tokens = 1234
        bar.cost = "$0.03"
        content = bar._format_content()
        assert "qwen3.5" in content
        assert "1,234" in content
        assert "$0.03" in content

    def test_format_content_with_streaming(self):
        bar = StatusBar()
        bar.model = "gpt-4"
        bar.is_streaming = True
        content = bar._format_content()
        assert "streaming…" in content
        assert "/help" in content
        assert "Ctrl+D quit" in content

    def test_format_content_with_vim_mode(self):
        bar = StatusBar()
        bar.vim_mode = "NORMAL"
        content = bar._format_content()
        assert "-- NORMAL --" in content

    def test_format_content_tokens_formatting(self):
        bar = StatusBar()
        bar.tokens = 0
        content = bar._format_content()
        assert "tok" not in content

        bar.tokens = 1000
        content = bar._format_content()
        assert "↓1,000 tok" in content

    def test_format_content_always_has_help_and_quit(self):
        bar = StatusBar()
        content = bar._format_content()
        assert "/help" in content
        assert "Ctrl+D quit" in content
