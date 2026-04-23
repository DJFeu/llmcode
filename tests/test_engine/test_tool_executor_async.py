"""Async tests for :class:`ToolExecutorComponent` (M5 — Task 5.4)."""
from __future__ import annotations

import asyncio

import pytest

from llm_code.engine.components.tool_executor import ToolExecutorComponent
from llm_code.tools.base import PermissionLevel, Tool, ToolResult


class _SyncTool(Tool):
    """Sync-only tool; default execute_async bridges via to_thread."""

    @property
    def name(self) -> str:
        return "sync_tool"

    @property
    def description(self) -> str:
        return ""

    @property
    def input_schema(self) -> dict:
        return {}

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    def execute(self, args: dict) -> ToolResult:
        return ToolResult(output=f"sync:{args.get('x')}")


class _AsyncTool(Tool):
    """Async-native tool; is_async=True skips the to_thread bridge."""

    is_async = True

    @property
    def name(self) -> str:
        return "async_tool"

    @property
    def description(self) -> str:
        return ""

    @property
    def input_schema(self) -> dict:
        return {}

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    def execute(self, args: dict) -> ToolResult:
        raise NotImplementedError("async-only tool — call execute_async")

    async def execute_async(self, args: dict) -> ToolResult:
        await asyncio.sleep(0)
        return ToolResult(output=f"async:{args.get('x')}")


class _BadIsAsyncTool(Tool):
    is_async = True

    @property
    def name(self) -> str:
        return "bad"

    @property
    def description(self) -> str:
        return ""

    @property
    def input_schema(self) -> dict:
        return {}

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    def execute(self, args: dict) -> ToolResult:
        return ToolResult(output="x")

    # Deliberately forget to override execute_async.


class TestToolExecutorAsync:
    async def test_sync_tool_bridged_via_to_thread(self):
        ex = ToolExecutorComponent()
        result = await ex.run_async(
            proceed=True,
            resolved_tool=_SyncTool(),
            tool_args={"x": 5},
            cached_result=None,
        )
        assert result["raw_result"].output == "sync:5"
        assert result["executed"] is True

    async def test_async_tool_awaited_directly(self):
        ex = ToolExecutorComponent()
        result = await ex.run_async(
            proceed=True,
            resolved_tool=_AsyncTool(),
            tool_args={"x": 7},
            cached_result=None,
        )
        assert result["raw_result"].output == "async:7"
        assert result["executed"] is True

    async def test_cached_result_short_circuits(self):
        ex = ToolExecutorComponent()
        cached = ToolResult(output="cached")
        result = await ex.run_async(
            proceed=True,
            resolved_tool=_AsyncTool(),
            tool_args={"x": 1},
            cached_result=cached,
        )
        assert result["raw_result"] is cached
        assert result["executed"] is False

    async def test_gate_denial(self):
        ex = ToolExecutorComponent()
        result = await ex.run_async(
            proceed=False,
            resolved_tool=_SyncTool(),
            tool_args={"x": 1},
            cached_result=None,
        )
        assert result["raw_result"].is_error is True
        assert result["raw_result"].metadata["source"] == "tool_executor_gate"
        assert result["executed"] is False

    async def test_no_resolved_tool(self):
        ex = ToolExecutorComponent()
        result = await ex.run_async(
            proceed=True,
            resolved_tool=None,
            tool_args={},
            cached_result=None,
        )
        assert result["raw_result"].is_error is True
        assert result["executed"] is False

    async def test_is_async_without_override_raises(self):
        ex = ToolExecutorComponent()
        with pytest.raises(NotImplementedError):
            await ex.run_async(
                proceed=True,
                resolved_tool=_BadIsAsyncTool(),
                tool_args={},
                cached_result=None,
            )

    async def test_exception_with_catch_errors(self):
        class _Boom(_SyncTool):
            def execute(self, args):
                raise RuntimeError("boom")

        ex = ToolExecutorComponent(catch_errors=True)
        result = await ex.run_async(
            proceed=True,
            resolved_tool=_Boom(),
            tool_args={},
            cached_result=None,
        )
        assert result["raw_result"].is_error is True
        assert result["executed"] is False
        assert "boom" in result["raw_result"].output


class TestToolBaseAsync:
    async def test_default_execute_async_bridges_sync(self):
        tool = _SyncTool()
        result = await tool.execute_async({"x": 42})
        assert result.output == "sync:42"

    async def test_async_override_is_awaited(self):
        tool = _AsyncTool()
        result = await tool.execute_async({"x": 7})
        assert result.output == "async:7"

    def test_is_async_attribute_default_false(self):
        assert _SyncTool.is_async is False
        assert _AsyncTool.is_async is True
