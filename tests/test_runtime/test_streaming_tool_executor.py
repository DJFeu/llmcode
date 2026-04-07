"""Tests for concurrent streaming tool executor."""
from __future__ import annotations

import asyncio
import time

import pytest

from llm_code.runtime.streaming_tool_executor import (
    CONCURRENT_SAFE,
    StreamingToolExecutor,
    ToolCall,
    is_concurrent_safe,
)


class TestSafetyClassification:
    def test_read_file_concurrent_safe(self) -> None:
        assert is_concurrent_safe("read_file")

    def test_lsp_prefix_concurrent_safe(self) -> None:
        assert is_concurrent_safe("lsp_anything")

    def test_bash_not_concurrent_safe(self) -> None:
        assert not is_concurrent_safe("bash")

    def test_write_file_not_concurrent_safe(self) -> None:
        assert not is_concurrent_safe("write_file")

    def test_concurrent_safe_is_frozen(self) -> None:
        assert isinstance(CONCURRENT_SAFE, frozenset)


class TestStreamingToolExecutor:
    @pytest.mark.asyncio
    async def test_dispatch_returns_result(self) -> None:
        executor = StreamingToolExecutor(max_concurrent=2)

        async def runner(call: ToolCall) -> str:
            return f"ok:{call.name}"

        result = await executor.dispatch(
            ToolCall(id="1", name="read_file", arguments={}),
            runner,
        )
        assert result.id == "1"
        assert result.output == "ok:read_file"
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_concurrent_safe_runs_in_parallel(self) -> None:
        executor = StreamingToolExecutor(max_concurrent=4)
        started: list[float] = []
        release = asyncio.Event()

        async def runner(call: ToolCall) -> str:
            started.append(time.monotonic())
            await release.wait()
            return "done"

        calls = [
            ToolCall(id=str(i), name="read_file") for i in range(4)
        ]
        task = asyncio.create_task(executor.dispatch_many(calls, runner))
        # Give the dispatcher a moment to start all tasks
        for _ in range(50):
            if len(started) == 4:
                break
            await asyncio.sleep(0.005)
        assert len(started) == 4, "all concurrent tools should start in parallel"
        release.set()
        results = await task
        assert len(results) == 4
        assert [r.id for r in results] == ["0", "1", "2", "3"]

    @pytest.mark.asyncio
    async def test_exclusive_tools_serialize(self) -> None:
        executor = StreamingToolExecutor(max_concurrent=4)
        log: list[str] = []

        async def runner(call: ToolCall) -> str:
            log.append(f"start:{call.id}")
            await asyncio.sleep(0.01)
            log.append(f"end:{call.id}")
            return "ok"

        calls = [ToolCall(id=str(i), name="bash") for i in range(3)]
        await executor.dispatch_many(calls, runner)
        # Serial: start0, end0, start1, end1, start2, end2
        assert log == [
            "start:0", "end:0",
            "start:1", "end:1",
            "start:2", "end:2",
        ]

    @pytest.mark.asyncio
    async def test_exclusive_waits_for_concurrent_drain(self) -> None:
        executor = StreamingToolExecutor(max_concurrent=4)
        order: list[str] = []
        read_release = asyncio.Event()

        async def runner(call: ToolCall) -> str:
            if call.name == "read_file":
                order.append(f"read-start:{call.id}")
                await read_release.wait()
                order.append(f"read-end:{call.id}")
            else:
                order.append(f"write-start:{call.id}")
                order.append(f"write-end:{call.id}")
            return "ok"

        calls = [
            ToolCall(id="r1", name="read_file"),
            ToolCall(id="r2", name="read_file"),
            ToolCall(id="w1", name="write_file"),
        ]
        task = asyncio.create_task(executor.dispatch_many(calls, runner))
        await asyncio.sleep(0.02)
        # Reads should be started, write should not have started yet
        assert "read-start:r1" in order
        assert "read-start:r2" in order
        assert "write-start:w1" not in order
        read_release.set()
        results = await task
        # Write should happen only after reads ended
        write_start_idx = order.index("write-start:w1")
        assert order.index("read-end:r1") < write_start_idx
        assert order.index("read-end:r2") < write_start_idx
        assert [r.id for r in results] == ["r1", "r2", "w1"]

    @pytest.mark.asyncio
    async def test_error_surfaced_as_result(self) -> None:
        executor = StreamingToolExecutor()

        async def runner(call: ToolCall) -> str:
            raise RuntimeError("boom")

        result = await executor.dispatch(
            ToolCall(id="x", name="read_file"), runner
        )
        assert result.is_error
        assert "boom" in (result.error or "")

    def test_invalid_max_concurrent(self) -> None:
        with pytest.raises(ValueError):
            StreamingToolExecutor(max_concurrent=0)
