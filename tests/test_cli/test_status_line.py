import pytest
from llm_code.cli.status_line import StatusLineState, format_status_line


class TestFormatStatusLine:
    def test_empty_state(self):
        state = StatusLineState()
        result = format_status_line(state)
        assert "/help" in result
        assert "Ctrl+D quit" in result

    def test_model_only(self):
        state = StatusLineState(model="qwen-72b")
        result = format_status_line(state)
        assert "qwen-72b" in result

    def test_full_state(self):
        state = StatusLineState(
            model="qwen-72b",
            tokens=1234,
            cost="$0.0050",
            is_streaming=True,
        )
        result = format_status_line(state)
        assert "qwen-72b" in result
        assert "1,234" in result
        assert "$0.0050" in result
        assert "streaming" in result

    def test_context_usage_hidden_below_threshold(self):
        state = StatusLineState(model="qwen-72b", context_usage=0.3)
        result = format_status_line(state)
        assert "%" not in result

    def test_context_usage_shown_above_threshold(self):
        state = StatusLineState(model="qwen-72b", context_usage=0.75)
        result = format_status_line(state)
        assert "75%" in result

    def test_permission_mode_shown(self):
        state = StatusLineState(model="qwen-72b", permission_mode="plan")
        result = format_status_line(state)
        assert "[plan]" in result


from unittest.mock import MagicMock
from rich.console import Console


class TestCLIStatusLine:
    def test_update_changes_state(self):
        from llm_code.cli.status_line import CLIStatusLine
        console = Console(file=MagicMock(), force_terminal=True)
        line = CLIStatusLine(console)
        line.update(model="test-model", tokens=500)
        assert line.state.model == "test-model"
        assert line.state.tokens == 500

    def test_update_partial(self):
        from llm_code.cli.status_line import CLIStatusLine
        console = Console(file=MagicMock(), force_terminal=True)
        line = CLIStatusLine(console)
        line.update(model="m1")
        line.update(tokens=100)
        assert line.state.model == "m1"
        assert line.state.tokens == 100
