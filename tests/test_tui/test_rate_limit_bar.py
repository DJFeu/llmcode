"""Tests for RateLimitBar widget rendering and ANSI/sandbox stripping."""
from __future__ import annotations

import time

from llm_code.tui.ansi_strip import strip_ansi
from llm_code.tui.chat_widgets import (
    RateLimitBar,
    ToolBlock,
    _clean_tool_result,
    _truncate_lines,
)


class _Tracker:
    def __init__(self, info):
        self.rate_limit_info = info


def test_rate_limit_bar_hidden_when_no_info():
    bar = RateLimitBar(cost_tracker=_Tracker(None))
    assert bar.render_text() == ""


def test_rate_limit_bar_renders_percent_and_reset():
    info = {"used": 65, "limit": 100, "reset_at": time.time() + 2 * 3600 + 15 * 60}
    bar = RateLimitBar(cost_tracker=_Tracker(info), width=12)
    text = bar.render_text()
    assert "65%" in text
    assert "resets in 2h 14m" in text or "resets in 2h 15m" in text
    # 12-char bar, ~65% filled = 8 blocks
    assert text.count("█") == 8
    assert text.count("░") == 4


def test_rate_limit_bar_zero_limit():
    bar = RateLimitBar(cost_tracker=_Tracker({"used": 0, "limit": 0}))
    assert bar.render_text() == ""


def test_strip_ansi_basic_sgr():
    assert strip_ansi("\x1b[31mhello\x1b[0m") == "hello"


def test_strip_ansi_leaves_plain_text():
    assert strip_ansi("plain text") == "plain text"


def test_strip_ansi_handles_malformed():
    # Lone ESC — shouldn't crash
    s = "abc\x1b def"
    out = strip_ansi(s)
    assert "abc" in out and "def" in out


def test_clean_tool_result_strips_sandbox_tag():
    raw = "ok <sandbox-violation>blocked: rm -rf</sandbox-violation> done"
    out = _clean_tool_result(raw)
    assert "<sandbox-violation>" not in out
    assert "blocked" not in out


def test_truncate_lines_counts_hidden():
    text = "\n".join(f"line{i}" for i in range(20))
    body, hidden = _truncate_lines(text, max_lines=8)
    assert hidden == 12
    assert body.count("\n") == 7


def test_truncate_lines_short_text():
    body, hidden = _truncate_lines("a\nb", max_lines=8)
    assert hidden == 0


def test_tool_block_error_truncation_renders():
    long = "\n".join(f"err{i}" for i in range(20))
    tb = ToolBlock.create("bash", "{'cmd': 'foo'}", long, is_error=True)
    rendered = tb.render()
    # Rich Text — convert to plain
    assert "+12 more lines" in rendered.plain or "+12 more line" in rendered.plain


def test_tool_block_verbose_shows_full():
    long = "\n".join(f"err{i}" for i in range(20))
    tb = ToolBlock.create("bash", "{'cmd': 'foo'}", long, is_error=True)
    tb.set_verbose(True)
    rendered = tb.render()
    assert "err19" in rendered.plain
