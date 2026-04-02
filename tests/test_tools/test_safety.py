"""Tests for Tool ABC safety/concurrency methods."""
from __future__ import annotations

from llm_code.tools.base import PermissionLevel, Tool, ToolProgress, ToolResult


class ConcreteTool(Tool):
    """Minimal concrete tool for testing ABC defaults."""

    @property
    def name(self) -> str:
        return "concrete_tool"

    @property
    def description(self) -> str:
        return "A test tool"

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    def execute(self, args: dict) -> ToolResult:
        return ToolResult(output="executed", metadata={"args": args})


class TestSafetyDefaults:
    def setup_method(self) -> None:
        self.tool = ConcreteTool()

    def test_is_read_only_defaults_false(self) -> None:
        assert self.tool.is_read_only({}) is False

    def test_is_destructive_defaults_false(self) -> None:
        assert self.tool.is_destructive({}) is False

    def test_is_concurrency_safe_defaults_false(self) -> None:
        assert self.tool.is_concurrency_safe({}) is False

    def test_is_read_only_with_args(self) -> None:
        assert self.tool.is_read_only({"path": "/tmp/foo"}) is False

    def test_is_destructive_with_args(self) -> None:
        assert self.tool.is_destructive({"path": "/tmp/foo"}) is False

    def test_is_concurrency_safe_with_args(self) -> None:
        assert self.tool.is_concurrency_safe({"path": "/tmp/foo"}) is False


class TestExecuteWithProgress:
    def setup_method(self) -> None:
        self.tool = ConcreteTool()

    def test_execute_with_progress_falls_back_to_execute(self) -> None:
        args = {"key": "value"}
        progress_calls: list[ToolProgress] = []

        result = self.tool.execute_with_progress(args, on_progress=progress_calls.append)

        assert result.output == "executed"
        assert result.metadata == {"args": args}
        assert progress_calls == []

    def test_execute_with_progress_no_progress_emitted_by_default(self) -> None:
        called: list[ToolProgress] = []
        self.tool.execute_with_progress({}, on_progress=lambda p: called.append(p))
        assert called == []

    def test_execute_with_progress_returns_tool_result(self) -> None:
        result = self.tool.execute_with_progress({}, on_progress=lambda _: None)
        assert isinstance(result, ToolResult)
