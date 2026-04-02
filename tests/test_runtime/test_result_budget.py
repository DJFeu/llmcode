"""Tests for tool result budget (large result truncation)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from llm_code.runtime.context import ProjectContext
from llm_code.runtime.conversation import ConversationRuntime
from llm_code.tools.base import ToolResult


def _make_runtime(tmp_path: Path) -> ConversationRuntime:
    """Create a minimal ConversationRuntime for testing _budget_tool_result."""
    from llm_code.runtime.permissions import PermissionMode, PermissionPolicy
    from llm_code.runtime.prompt import SystemPromptBuilder
    from llm_code.runtime.session import Session
    from llm_code.tools.registry import ToolRegistry

    context = ProjectContext(
        cwd=tmp_path,
        is_git_repo=False,
        git_status="",
        instructions="",
    )

    class _Config:
        max_turn_iterations = 5
        max_tokens = 4096
        temperature = 0.0
        model = "test"
        native_tools = True

    return ConversationRuntime(
        provider=MagicMock(),
        tool_registry=ToolRegistry(),
        permission_policy=PermissionPolicy(mode=PermissionMode.AUTO_ACCEPT),
        hook_runner=MagicMock(),
        prompt_builder=SystemPromptBuilder(),
        config=_Config(),
        session=Session.create(tmp_path),
        context=context,
    )


class TestResultBudget:
    def test_small_result_unchanged(self, tmp_path: Path) -> None:
        runtime = _make_runtime(tmp_path)
        small_output = "x" * 100
        result = ToolResult(output=small_output)
        budgeted = runtime._budget_tool_result(result, "call1")
        assert budgeted.output == small_output
        assert budgeted is result  # same object — not replaced

    def test_large_result_truncated(self, tmp_path: Path) -> None:
        runtime = _make_runtime(tmp_path)
        large_output = "y" * 10_000
        result = ToolResult(output=large_output)
        budgeted = runtime._budget_tool_result(result, "call2")
        # Output should be shorter than original
        assert len(budgeted.output) < len(large_output)
        # First 1000 chars preserved
        assert budgeted.output.startswith("y" * 1000)
        # Pointer message present
        assert "10000 chars total" in budgeted.output

    def test_large_result_saved_to_disk(self, tmp_path: Path) -> None:
        runtime = _make_runtime(tmp_path)
        large_output = "z" * 10_000
        result = ToolResult(output=large_output)
        runtime._budget_tool_result(result, "call3")
        cache_path = tmp_path / ".llm-code" / "result_cache" / "call3.txt"
        assert cache_path.exists()
        assert cache_path.read_text(encoding="utf-8") == large_output

    def test_cache_dir_created(self, tmp_path: Path) -> None:
        runtime = _make_runtime(tmp_path)
        large_output = "a" * 5_000
        result = ToolResult(output=large_output)
        runtime._budget_tool_result(result, "call4")
        cache_dir = tmp_path / ".llm-code" / "result_cache"
        assert cache_dir.is_dir()

    def test_error_result_preserved(self, tmp_path: Path) -> None:
        runtime = _make_runtime(tmp_path)
        large_output = "e" * 10_000
        result = ToolResult(output=large_output, is_error=True)
        budgeted = runtime._budget_tool_result(result, "call5")
        assert budgeted.is_error is True

    def test_metadata_preserved(self, tmp_path: Path) -> None:
        runtime = _make_runtime(tmp_path)
        large_output = "m" * 10_000
        result = ToolResult(output=large_output, metadata={"key": "val"})
        budgeted = runtime._budget_tool_result(result, "call6")
        assert budgeted.metadata == {"key": "val"}
