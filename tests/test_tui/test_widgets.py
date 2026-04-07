"""Tests for TUI widgets."""
from __future__ import annotations

from llm_code.tui.header_bar import HeaderBar
from llm_code.tui.status_bar import StatusBar
from llm_code.tui.chat_widgets import ToolBlock, ThinkingBlock, TurnSummary, SpinnerLine
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
        assert "Read" in rendered
        assert "/src/main.py" in rendered
        assert "●" in rendered

    def test_format_error(self):
        block = ToolBlock.create("bash", "$ rm -rf /", "Permission denied", is_error=True)
        rendered = block.render_text()
        assert "✗" in rendered

    def test_format_bash(self):
        block = ToolBlock.create("bash", "ls -la", "total 42", is_error=False)
        rendered = block.render_text()
        assert "Bash" in rendered

    def test_format_edit_with_diff(self):
        block = ToolBlock.create(
            "edit_file", "{'path': '/src/app.py'}", "Updated",
            is_error=False, diff_lines=["-old line", "+new line", " context"],
        )
        rendered = str(block.render())
        assert "Update" in rendered
        assert "/src/app.py" in rendered


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
        # Thinking phase now uses a randomly-picked whimsical verb.
        s.phase = "thinking"
        out = s.render_text()
        assert out.split("…", 1)[0]  # non-empty verb followed by ellipsis
        assert "…" in out
        s.phase = "processing"
        out2 = s.render_text()
        assert "…" in out2

    def test_stalled_color_leans_red(self):
        s = SpinnerLine()
        s.elapsed = 5
        s._last_progress = 5
        r1, _, _ = s._stall_rgb()
        s.elapsed = 70  # 65s of stall
        r2, _, _ = s._stall_rgb()
        assert r2 > r1


def _simulate_key(bar: "InputBar", key: str) -> None:
    """Simulate a key press on InputBar without a running Textual App.

    Creates a minimal Key-like object and calls the on_key handler directly.
    """
    from unittest.mock import MagicMock
    event = MagicMock()
    event.key = key
    # For single printable characters, set event.character
    if len(key) == 1 and key.isprintable():
        event.character = key
    else:
        event.character = None
    # Stub out post_message to prevent Textual runtime errors
    bar.post_message = MagicMock()
    bar.on_key(event)


class TestInputBar:
    def test_creates(self):
        bar = InputBar()
        assert bar.value == ""

    def test_prompt_symbol(self):
        bar = InputBar()
        assert bar.PROMPT == "❯ "

    def test_render_default(self):
        bar = InputBar()
        rendered = str(bar.render())
        assert "❯" in rendered
        assert "█" in rendered

    def test_render_vim_normal(self):
        bar = InputBar()
        bar.vim_mode = "NORMAL"
        rendered = str(bar.render())
        assert "[N]" in rendered

    def test_render_vim_insert(self):
        bar = InputBar()
        bar.vim_mode = "INSERT"
        rendered = str(bar.render())
        assert "[I]" in rendered

    def test_render_disabled(self):
        bar = InputBar()
        bar.disabled = True
        rendered = str(bar.render())
        assert "generating" in rendered
        assert "█" not in rendered

    def test_on_key_character(self):
        bar = InputBar()
        _simulate_key(bar, "a")
        _simulate_key(bar, "b")
        assert bar.value == "ab"

    def test_on_key_backspace(self):
        bar = InputBar()
        bar.value = "hello"
        bar._cursor = 5  # cursor at end
        _simulate_key(bar, "backspace")
        assert bar.value == "hell"

    def test_on_key_backspace_empty(self):
        bar = InputBar()
        _simulate_key(bar, "backspace")
        assert bar.value == ""

    def test_on_key_shift_enter_multiline(self):
        bar = InputBar()
        bar.value = "line1"
        bar._cursor = 5
        _simulate_key(bar, "shift+enter")
        assert bar.value == "line1\n"

    def test_on_key_enter_submits_and_clears(self):
        bar = InputBar()
        bar.value = "hello"
        bar._cursor = 5
        _simulate_key(bar, "enter")
        assert bar.value == ""

    def test_on_key_enter_ignores_whitespace_only(self):
        bar = InputBar()
        bar.value = "   "
        bar._cursor = 3
        _simulate_key(bar, "enter")
        assert bar.value == "   "

    def test_on_key_escape_clears_and_cancels(self):
        bar = InputBar()
        bar.value = "draft"
        bar._cursor = 5
        _simulate_key(bar, "escape")
        assert bar.value == ""

    def test_cursor_movement(self):
        bar = InputBar()
        bar.value = "hello"
        bar._cursor = 5
        _simulate_key(bar, "left")
        assert bar._cursor == 4
        _simulate_key(bar, "left")
        assert bar._cursor == 3
        # Insert at cursor
        _simulate_key(bar, "x")
        assert bar.value == "helxlo"
        assert bar._cursor == 4
        # Backspace at cursor
        _simulate_key(bar, "backspace")
        assert bar.value == "hello"
        assert bar._cursor == 3

    def test_on_key_disabled_blocks_input(self):
        bar = InputBar()
        bar.disabled = True
        _simulate_key(bar, "a")
        assert bar.value == ""
