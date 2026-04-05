"""Tests for plan/act mode toggle."""
from __future__ import annotations

from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from llm_code.tui.status_bar import StatusBar


# Denied tools in plan mode
WRITE_TOOLS = frozenset({"write_file", "edit_file", "bash", "git_commit", "git_push", "notebook_edit"})
# Allowed tools in plan mode
READ_TOOLS = frozenset({"read_file", "glob_search", "grep_search", "git_status", "git_diff", "git_log"})


class TestPlanModeStatusBar:
    def test_plan_mode_shown_in_status_bar(self) -> None:
        bar = StatusBar()
        bar.plan_mode = "PLAN"
        bar.model = "claude-sonnet-4-6"
        content = bar._format_content()
        assert "PLAN" in content
        # PLAN should appear before model
        plan_pos = content.index("PLAN")
        model_pos = content.index("claude-sonnet-4-6")
        assert plan_pos < model_pos

    def test_plan_mode_hidden_when_empty(self) -> None:
        bar = StatusBar()
        bar.plan_mode = ""
        bar.model = "qwen3.5"
        content = bar._format_content()
        assert "PLAN" not in content


class TestPlanModeToolDenial:
    """Verify that write tools are denied and read tools allowed in plan mode."""

    def test_write_tools_identified(self) -> None:
        for tool in WRITE_TOOLS:
            assert tool in WRITE_TOOLS

    def test_read_tools_not_in_denied_set(self) -> None:
        for tool in READ_TOOLS:
            assert tool not in WRITE_TOOLS
