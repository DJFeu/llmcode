"""Tests for real-time token/cost display in StatusBar."""
from __future__ import annotations

import pytest

from llm_code.tui.status_bar import StatusBar


class TestStatusBarCostFormatting:
    """Verify StatusBar formats cost in different states."""

    def test_cost_displayed_when_positive(self) -> None:
        bar = StatusBar()
        bar.model = "claude-sonnet-4-6"
        bar.tokens = 12345
        bar.cost = "$0.0042"
        content = bar._format_content()
        assert "$0.0042" in content
        assert "12,345" in content

    def test_free_displayed_when_local(self) -> None:
        bar = StatusBar()
        bar.model = "qwen3.5"
        bar.tokens = 5000
        bar.is_local = True
        content = bar._format_content()
        assert "free" in content
        assert "$" not in content

    def test_cost_omitted_when_zero_and_not_local(self) -> None:
        bar = StatusBar()
        bar.model = "gpt-4o"
        bar.tokens = 0
        bar.is_local = False
        content = bar._format_content()
        assert "free" not in content
        assert "$" not in content

    def test_plan_mode_not_shown_by_default(self) -> None:
        bar = StatusBar()
        bar.model = "claude-sonnet-4-6"
        content = bar._format_content()
        assert "PLAN" not in content
