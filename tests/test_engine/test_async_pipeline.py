"""Tests for :class:`llm_code.engine.async_pipeline.AsyncPipeline` (M5 — Task 5.3)."""
from __future__ import annotations

import asyncio
import time

import pytest

from llm_code.engine.async_component import async_component
from llm_code.engine.async_pipeline import AsyncPipeline, run_via_async
from llm_code.engine.component import component, output_types


# ---------------------------------------------------------------------------
# Fixtures — component doubles
# ---------------------------------------------------------------------------


@component
@output_types(sum=int)
class _Adder:
    def run(self, a: int, b: int) -> dict:
        return {"sum": a + b}


@component
@output_types(doubled=int)
class _Doubler:
    def run(self, x: int) -> dict:
        return {"doubled": x * 2}


@component
@async_component
@output_types(value=int)
class _SlowAsync:
    concurrency_group = "io"

    async def run_async(self, x: int) -> dict:
        await asyncio.sleep(0.02)
        return {"value": x + 100}


@component
@async_component
@output_types(value=int)
class _SlowAsyncB:
    concurrency_group = "io"

    async def run_async(self, x: int) -> dict:
        await asyncio.sleep(0.02)
        return {"value": x + 200}


# ---------------------------------------------------------------------------
# Basic execution
# ---------------------------------------------------------------------------


class TestAsyncPipelineBasic:
    async def test_runs_single_component(self):
        pipe = AsyncPipeline()
        pipe.add_component("add", _Adder())
        result = await pipe.run_async({"add": {"a": 2, "b": 3}})
        assert result == {"add": {"sum": 5}}

    async def test_runs_connected_components_in_topological_order(self):
        pipe = AsyncPipeline()
        pipe.add_component("add", _Adder())
        pipe.add_component("dbl", _Doubler())
        pipe.connect("add.sum", "dbl.x")
        result = await pipe.run_async({"add": {"a": 2, "b": 3}})
        assert result["add"] == {"sum": 5}
        assert result["dbl"] == {"doubled": 10}

    async def test_preserves_none_result_as_empty_dict(self):
        @component
        class _Silent:
            def run(self) -> None:
                return None

        pipe = AsyncPipeline()
        pipe.add_component("s", _Silent())
        result = await pipe.run_async({})
        assert result["s"] == {}


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


class TestAsyncPipelineConcurrency:
    async def test_same_level_same_group_run_in_parallel(self):
        pipe = AsyncPipeline()
        pipe.add_component("a", _SlowAsync())
        pipe.add_component("b", _SlowAsyncB())
        t0 = time.monotonic()
        result = await pipe.run_async({"a": {"x": 1}, "b": {"x": 2}})
        elapsed = time.monotonic() - t0
        # Two 20 ms sleeps in parallel should finish < 35 ms.
        assert elapsed < 0.035
        assert result["a"] == {"value": 101}
        assert result["b"] == {"value": 202}

    async def test_level_boundary_is_sequential(self):
        # dbl depends on add → they're on different levels, so total
        # time = max(level0) + max(level1). With only the slow async
        # on level 0 we should see at least ~20ms elapsed.
        pipe = AsyncPipeline()
        pipe.add_component("a", _SlowAsync())
        pipe.add_component("dbl", _Doubler())
        pipe.connect("a.value", "dbl.x")
        t0 = time.monotonic()
        await pipe.run_async({"a": {"x": 1}})
        assert time.monotonic() - t0 >= 0.018


# ---------------------------------------------------------------------------
# Cycle detection / error propagation
# ---------------------------------------------------------------------------


class TestAsyncPipelineErrors:
    async def test_component_exception_propagates(self):
        @component
        class _Bad:
            def run(self) -> dict:
                raise ValueError("no good")

        pipe = AsyncPipeline()
        pipe.add_component("bad", _Bad())
        with pytest.raises(ValueError, match="no good"):
            await pipe.run_async({})

    async def test_cycle_raises_runtime_error(self):
        pipe = AsyncPipeline()
        pipe.add_component("a", _Doubler())
        pipe.add_component("b", _Doubler())
        pipe.connect("a.doubled", "b.x")
        # Manually force a cycle via underlying graph (normal wiring
        # prevents it; we bypass for the cycle case).
        pipe._graph.add_edge("b", "a")
        with pytest.raises(RuntimeError, match="cycle"):
            await pipe.run_async({"a": {"x": 1}})


# ---------------------------------------------------------------------------
# Sync bridge
# ---------------------------------------------------------------------------


class TestRunViaAsync:
    def test_bridges_sync_pipeline_through_async_engine(self):
        # Use the base Pipeline + the bridge helper.
        from llm_code.engine.pipeline import Pipeline

        pipe = Pipeline()
        pipe.add_component("add", _Adder())
        result = run_via_async(pipe, {"add": {"a": 4, "b": 5}})
        assert result == {"add": {"sum": 9}}

    async def test_run_via_async_from_loop_raises(self):
        from llm_code.engine.pipeline import Pipeline

        pipe = Pipeline()
        pipe.add_component("add", _Adder())
        with pytest.raises(RuntimeError, match="running loop"):
            run_via_async(pipe, {"add": {"a": 1, "b": 2}})


# ---------------------------------------------------------------------------
# Level computation
# ---------------------------------------------------------------------------


class TestComputeLevels:
    def test_straight_line_yields_one_per_level(self):
        pipe = AsyncPipeline()
        pipe.add_component("a", _Adder())
        pipe.add_component("b", _Doubler())
        pipe.connect("a.sum", "b.x")
        order = pipe._graph.topological_sort()
        levels = pipe._compute_levels(order)
        assert levels == [["a"], ["b"]]

    def test_diamond_shape(self):
        @component
        @output_types(x=int)
        class _Src:
            def run(self) -> dict:
                return {"x": 1}

        @component
        @output_types(combined=int)
        class _Sink:
            def run(self, a: int, b: int) -> dict:
                return {"combined": a + b}

        pipe = AsyncPipeline()
        pipe.add_component("src", _Src())
        pipe.add_component("l", _Doubler())
        pipe.add_component("r", _Doubler())
        pipe.add_component("sink", _Sink())
        pipe.connect("src.x", "l.x")
        pipe.connect("src.x", "r.x")
        pipe.connect("l.doubled", "sink.a")
        pipe.connect("r.doubled", "sink.b")
        order = pipe._graph.topological_sort()
        levels = pipe._compute_levels(order)
        assert levels[0] == ["src"]
        assert set(levels[1]) == {"l", "r"}
        assert levels[2] == ["sink"]
