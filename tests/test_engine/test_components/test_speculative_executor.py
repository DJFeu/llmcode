"""Tests for :class:`SpeculativeExecutorComponent` — v12 M2 Task 2.7 Step 3.

This Component implements the v11 "speculative execution cache" — not
the OverlayFS-based pre-execution in :mod:`llm_code.runtime.speculative`
(which stays as an orthogonal feature). Identity is
``(tool_name, sha256(canonical_json(tool_args)))``. A hit returns the
cached :class:`ToolResult` directly; a miss is a pass-through so the
downstream :class:`ToolExecutorComponent` can run the call.

Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-pipeline-dag.md Task 2.7 Step 3
"""
from __future__ import annotations

import hashlib
import json


from llm_code.tools.base import ToolResult


class TestSpeculativeExecutorComponentImports:
    def test_module_imports(self) -> None:
        from llm_code.engine.components import (
            speculative_executor as se_mod,
        )

        assert hasattr(se_mod, "SpeculativeExecutorComponent")


class TestSpeculativeExecutorComponentShape:
    def test_marked_as_component(self) -> None:
        from llm_code.engine.component import is_component
        from llm_code.engine.components.speculative_executor import (
            SpeculativeExecutorComponent,
        )

        assert is_component(SpeculativeExecutorComponent())

    def test_input_sockets(self) -> None:
        from llm_code.engine.component import get_input_sockets
        from llm_code.engine.components.speculative_executor import (
            SpeculativeExecutorComponent,
        )

        inputs = get_input_sockets(SpeculativeExecutorComponent)
        assert set(inputs) >= {"proceed", "tool_name", "tool_args"}

    def test_output_sockets(self) -> None:
        from llm_code.engine.component import get_output_sockets
        from llm_code.engine.components.speculative_executor import (
            SpeculativeExecutorComponent,
        )

        outputs = get_output_sockets(SpeculativeExecutorComponent)
        assert set(outputs) == {"cache_hit", "cached_result", "proceed"}


class TestSpeculativeCacheKey:
    def test_cache_key_stable_for_same_input(self) -> None:
        from llm_code.engine.components.speculative_executor import (
            _cache_key,
        )

        k1 = _cache_key("read_file", {"path": "a"})
        k2 = _cache_key("read_file", {"path": "a"})
        assert k1 == k2

    def test_cache_key_ignores_dict_order(self) -> None:
        from llm_code.engine.components.speculative_executor import (
            _cache_key,
        )

        k1 = _cache_key("bash", {"cmd": "ls", "cwd": "/"})
        k2 = _cache_key("bash", {"cwd": "/", "cmd": "ls"})
        assert k1 == k2

    def test_cache_key_differs_by_tool(self) -> None:
        from llm_code.engine.components.speculative_executor import (
            _cache_key,
        )

        assert _cache_key("read_file", {"path": "a"}) != _cache_key(
            "write_file", {"path": "a"},
        )

    def test_cache_key_differs_by_args(self) -> None:
        from llm_code.engine.components.speculative_executor import (
            _cache_key,
        )

        assert _cache_key("read_file", {"path": "a"}) != _cache_key(
            "read_file", {"path": "b"},
        )

    def test_cache_key_uses_sha256(self) -> None:
        from llm_code.engine.components.speculative_executor import (
            _cache_key,
        )

        payload = json.dumps(
            {"t": "read_file", "a": {"path": "a"}}, sort_keys=True, default=str,
        ).encode()
        expected = hashlib.sha256(payload).hexdigest()
        assert _cache_key("read_file", {"path": "a"}) == expected


class TestSpeculativeExecutorComponentRun:
    def test_miss_returns_pass_through(self) -> None:
        from llm_code.engine.components.speculative_executor import (
            SpeculativeExecutorComponent,
        )

        comp = SpeculativeExecutorComponent()
        out = comp.run(
            proceed=True, tool_name="read_file", tool_args={"path": "a"},
        )
        assert out["cache_hit"] is False
        assert out["cached_result"] is None
        assert out["proceed"] is True

    def test_cache_populate_then_hit(self) -> None:
        from llm_code.engine.components.speculative_executor import (
            SpeculativeExecutorComponent,
        )

        comp = SpeculativeExecutorComponent()
        comp.store("read_file", {"path": "a"}, ToolResult(output="hello"))
        out = comp.run(
            proceed=True, tool_name="read_file", tool_args={"path": "a"},
        )
        assert out["cache_hit"] is True
        assert out["cached_result"].output == "hello"
        assert out["proceed"] is False  # downstream executor must skip

    def test_miss_for_different_args(self) -> None:
        from llm_code.engine.components.speculative_executor import (
            SpeculativeExecutorComponent,
        )

        comp = SpeculativeExecutorComponent()
        comp.store("read_file", {"path": "a"}, ToolResult(output="x"))
        out = comp.run(
            proceed=True, tool_name="read_file", tool_args={"path": "b"},
        )
        assert out["cache_hit"] is False

    def test_proceed_false_bypasses_cache(self) -> None:
        from llm_code.engine.components.speculative_executor import (
            SpeculativeExecutorComponent,
        )

        comp = SpeculativeExecutorComponent()
        comp.store("read_file", {"path": "a"}, ToolResult(output="x"))
        out = comp.run(
            proceed=False, tool_name="read_file", tool_args={"path": "a"},
        )
        # Denied upstream — we propagate that and don't pretend we served
        # from cache.
        assert out["cache_hit"] is False
        assert out["proceed"] is False

    def test_store_overwrites_previous_entry(self) -> None:
        from llm_code.engine.components.speculative_executor import (
            SpeculativeExecutorComponent,
        )

        comp = SpeculativeExecutorComponent()
        comp.store("t", {"x": 1}, ToolResult(output="v1"))
        comp.store("t", {"x": 1}, ToolResult(output="v2"))
        out = comp.run(proceed=True, tool_name="t", tool_args={"x": 1})
        assert out["cached_result"].output == "v2"

    def test_clear_empties_cache(self) -> None:
        from llm_code.engine.components.speculative_executor import (
            SpeculativeExecutorComponent,
        )

        comp = SpeculativeExecutorComponent()
        comp.store("t", {}, ToolResult(output="x"))
        comp.clear()
        out = comp.run(proceed=True, tool_name="t", tool_args={})
        assert out["cache_hit"] is False

    def test_max_size_evicts_oldest(self) -> None:
        from llm_code.engine.components.speculative_executor import (
            SpeculativeExecutorComponent,
        )

        comp = SpeculativeExecutorComponent(max_size=2)
        comp.store("t", {"i": 1}, ToolResult(output="1"))
        comp.store("t", {"i": 2}, ToolResult(output="2"))
        comp.store("t", {"i": 3}, ToolResult(output="3"))  # evicts i=1
        out = comp.run(proceed=True, tool_name="t", tool_args={"i": 1})
        assert out["cache_hit"] is False
        out = comp.run(proceed=True, tool_name="t", tool_args={"i": 3})
        assert out["cache_hit"] is True

    def test_hit_updates_recent_use_under_lru(self) -> None:
        from llm_code.engine.components.speculative_executor import (
            SpeculativeExecutorComponent,
        )

        comp = SpeculativeExecutorComponent(max_size=2)
        comp.store("t", {"i": 1}, ToolResult(output="1"))
        comp.store("t", {"i": 2}, ToolResult(output="2"))
        # Touch i=1 so i=2 becomes the eviction target.
        comp.run(proceed=True, tool_name="t", tool_args={"i": 1})
        comp.store("t", {"i": 3}, ToolResult(output="3"))
        out = comp.run(proceed=True, tool_name="t", tool_args={"i": 1})
        assert out["cache_hit"] is True
        out = comp.run(proceed=True, tool_name="t", tool_args={"i": 2})
        assert out["cache_hit"] is False

    def test_stats_report(self) -> None:
        from llm_code.engine.components.speculative_executor import (
            SpeculativeExecutorComponent,
        )

        comp = SpeculativeExecutorComponent()
        comp.store("t", {}, ToolResult(output="x"))
        comp.run(proceed=True, tool_name="t", tool_args={})
        comp.run(proceed=True, tool_name="other", tool_args={})
        stats = comp.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["size"] == 1

    def test_complex_args_hashable(self) -> None:
        from llm_code.engine.components.speculative_executor import (
            SpeculativeExecutorComponent,
        )

        comp = SpeculativeExecutorComponent()
        args = {"nested": {"a": [1, 2, 3]}, "flag": True}
        comp.store("t", args, ToolResult(output="nested"))
        out = comp.run(proceed=True, tool_name="t", tool_args=args)
        assert out["cache_hit"] is True


class TestSpeculativeInPipeline:
    def test_add_and_wire(self) -> None:
        from llm_code.engine.components.rate_limiter import RateLimiterComponent
        from llm_code.engine.components.speculative_executor import (
            SpeculativeExecutorComponent,
        )
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("rate", RateLimiterComponent())
        p.add_component("spec", SpeculativeExecutorComponent())
        p.connect("rate.proceed", "spec.proceed")
        # Sanity: spec still exposes its tool_name/tool_args entries.
        assert "tool_name" in p.inputs()["spec"]

    def test_pipeline_run_miss_pass_through(self) -> None:
        from llm_code.engine.components.speculative_executor import (
            SpeculativeExecutorComponent,
        )
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("spec", SpeculativeExecutorComponent())
        outputs = p.run({
            "spec": {
                "proceed": True,
                "tool_name": "read_file",
                "tool_args": {"path": "a"},
            },
        })
        assert outputs["spec"]["cache_hit"] is False
