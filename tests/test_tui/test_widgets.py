"""Tests for TUI widgets."""
from __future__ import annotations

from llm_code.tui.header_bar import HeaderBar
from llm_code.tui.status_bar import StatusBar
from llm_code.tui.chat_widgets import ToolBlock, ThinkingBlock, TurnSummary, SpinnerLine
from llm_code.tui.chat_view import ChatScrollView
from llm_code.tui.input_bar import InputBar


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


class TestToolBlock:
    def test_format_standard(self):
        block = ToolBlock.create("read_file", "{'path': '/src/main.py'}", "Read 45 lines", is_error=False)
        rendered = block.render_text()
        assert "┌ read_file" in rendered
        assert "Read 45 lines" in rendered
        assert "✓" in rendered

    def test_format_error(self):
        block = ToolBlock.create("bash", "$ rm -rf /", "Permission denied", is_error=True)
        rendered = block.render_text()
        assert "✗" in rendered

    def test_format_bash(self):
        block = ToolBlock.create("bash", "ls -la", "total 42", is_error=False)
        rendered = block.render_text()
        assert "$ ls -la" in rendered


class TestThinkingBlock:
    def test_collapsed_format(self):
        block = ThinkingBlock(content="deep thoughts", elapsed=3.2, tokens=500)
        collapsed = block.collapsed_text()
        assert "3.2s" in collapsed
        assert "500" in collapsed

    def test_toggle(self):
        block = ThinkingBlock(content="deep thoughts", elapsed=3.2, tokens=500)
        assert not block.expanded
        block.toggle()
        assert block.expanded


class TestTurnSummary:
    def test_format(self):
        summary = TurnSummary.create(elapsed=3.2, input_tokens=2400, output_tokens=890, cost="$0.03")
        text = summary.render_text()
        assert "3.2s" in text
        assert "2,400" in text
        assert "890" in text
        assert "$0.03" in text


class TestSpinnerLine:
    def test_phases(self):
        s = SpinnerLine()
        s.phase = "waiting"
        assert "Waiting" in s.render_text()
        s.phase = "thinking"
        assert "Thinking" in s.render_text()
        s.phase = "processing"
        assert "Processing" in s.render_text()


class TestInputBar:
    def test_creates(self):
        bar = InputBar()
        assert bar.value == ""

    def test_prompt_symbol(self):
        bar = InputBar()
        assert bar.PROMPT == "❯ "
