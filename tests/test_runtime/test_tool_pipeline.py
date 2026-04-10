"""Tests for the extracted ToolExecutionPipeline."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from llm_code.tools.base import ToolResult


def test_tool_pipeline_exists():
    from llm_code.runtime.tool_pipeline import ToolExecutionPipeline
    runtime = MagicMock()
    pipeline = ToolExecutionPipeline(runtime)
    assert pipeline._runtime is runtime


def test_budget_result_returns_same_when_small():
    from llm_code.runtime.tool_pipeline import ToolExecutionPipeline
    runtime = MagicMock()
    pipeline = ToolExecutionPipeline(runtime)
    result = ToolResult(output="small output", is_error=False)
    budgeted = pipeline.budget_result(result, "call_123")
    assert budgeted.output == "small output"
    assert budgeted is result  # same object when small


def test_budget_result_truncates_large(tmp_path: Path):
    from llm_code.runtime.tool_pipeline import ToolExecutionPipeline
    runtime = MagicMock()
    runtime._context.cwd = tmp_path
    pipeline = ToolExecutionPipeline(runtime)
    large_output = "x" * 10_000
    result = ToolResult(output=large_output, is_error=False)
    budgeted = pipeline.budget_result(result, "call_456")
    assert len(budgeted.output) < len(large_output)
    assert budgeted.output.startswith("x" * 1000)
    assert "10000 chars total" in budgeted.output


def test_budget_result_saves_to_disk(tmp_path: Path):
    from llm_code.runtime.tool_pipeline import ToolExecutionPipeline
    runtime = MagicMock()
    runtime._context.cwd = tmp_path
    pipeline = ToolExecutionPipeline(runtime)
    large_output = "z" * 5_000
    result = ToolResult(output=large_output, is_error=False)
    pipeline.budget_result(result, "call_789")
    cache_path = tmp_path / ".llmcode" / "result_cache" / "call_789.txt"
    assert cache_path.exists()
    assert cache_path.read_text(encoding="utf-8") == large_output


def test_budget_result_preserves_error_flag(tmp_path: Path):
    from llm_code.runtime.tool_pipeline import ToolExecutionPipeline
    runtime = MagicMock()
    runtime._context.cwd = tmp_path
    pipeline = ToolExecutionPipeline(runtime)
    result = ToolResult(output="e" * 10_000, is_error=True)
    budgeted = pipeline.budget_result(result, "call_err")
    assert budgeted.is_error is True


def test_budget_result_preserves_metadata(tmp_path: Path):
    from llm_code.runtime.tool_pipeline import ToolExecutionPipeline
    runtime = MagicMock()
    runtime._context.cwd = tmp_path
    pipeline = ToolExecutionPipeline(runtime)
    result = ToolResult(output="m" * 10_000, metadata={"key": "val"})
    budgeted = pipeline.budget_result(result, "call_meta")
    assert budgeted.metadata == {"key": "val"}
