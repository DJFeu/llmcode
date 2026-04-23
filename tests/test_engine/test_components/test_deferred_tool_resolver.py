"""Tests for :class:`DeferredToolResolverComponent` — v12 M2 Task 2.7 Step 4.

Implements v11's "deferred tools" behaviour: tool schemas are resolved
on demand instead of being loaded up front. A small LRU cache keeps the
last-N resolved schemas hot so repeated hits don't re-query the registry.

Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-pipeline-dag.md Task 2.7 Step 4
"""
from __future__ import annotations

import pytest


class _FakeTool:
    """Stand-in for a real :class:`llm_code.tools.base.Tool`.

    The tests care only that the resolver returns the registered object;
    they don't invoke the tool. Using a trivial class keeps the test
    surface tiny.
    """
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeRegistry:
    """Minimal stand-in for ``ToolRegistry.get``.

    Counts lookups so tests can assert the LRU cache actually elides
    repeated queries.
    """
    def __init__(self, tools: dict[str, _FakeTool]) -> None:
        self._tools = tools
        self.calls: list[str] = []

    def get(self, name: str) -> _FakeTool | None:
        self.calls.append(name)
        return self._tools.get(name)


class TestDeferredToolResolverComponentImports:
    def test_module_imports(self) -> None:
        from llm_code.engine.components import (
            deferred_tool_resolver as dtr_mod,
        )

        assert hasattr(dtr_mod, "DeferredToolResolverComponent")


class TestDeferredToolResolverComponentShape:
    def test_marked_as_component(self) -> None:
        from llm_code.engine.component import is_component
        from llm_code.engine.components.deferred_tool_resolver import (
            DeferredToolResolverComponent,
        )

        reg = _FakeRegistry({})
        assert is_component(DeferredToolResolverComponent(reg))

    def test_input_sockets(self) -> None:
        from llm_code.engine.component import get_input_sockets
        from llm_code.engine.components.deferred_tool_resolver import (
            DeferredToolResolverComponent,
        )

        inputs = get_input_sockets(DeferredToolResolverComponent)
        assert "tool_name" in inputs
        assert "cache_hit" in inputs
        assert "proceed" in inputs

    def test_output_sockets(self) -> None:
        from llm_code.engine.component import get_output_sockets
        from llm_code.engine.components.deferred_tool_resolver import (
            DeferredToolResolverComponent,
        )

        outputs = get_output_sockets(DeferredToolResolverComponent)
        assert set(outputs) == {"resolved_tool", "proceed", "resolution_error"}


class TestDeferredToolResolverRun:
    def test_resolves_known_tool(self) -> None:
        from llm_code.engine.components.deferred_tool_resolver import (
            DeferredToolResolverComponent,
        )

        tool = _FakeTool("bash")
        reg = _FakeRegistry({"bash": tool})
        comp = DeferredToolResolverComponent(reg)
        out = comp.run(proceed=True, cache_hit=False, tool_name="bash")
        assert out["resolved_tool"] is tool
        assert out["proceed"] is True
        assert out["resolution_error"] == ""

    def test_unknown_tool_denies(self) -> None:
        from llm_code.engine.components.deferred_tool_resolver import (
            DeferredToolResolverComponent,
        )

        reg = _FakeRegistry({})
        comp = DeferredToolResolverComponent(reg)
        out = comp.run(proceed=True, cache_hit=False, tool_name="nonexistent")
        assert out["resolved_tool"] is None
        assert out["proceed"] is False
        assert "nonexistent" in out["resolution_error"]

    def test_cache_hit_skips_resolution(self) -> None:
        from llm_code.engine.components.deferred_tool_resolver import (
            DeferredToolResolverComponent,
        )

        reg = _FakeRegistry({"bash": _FakeTool("bash")})
        comp = DeferredToolResolverComponent(reg)
        out = comp.run(proceed=True, cache_hit=True, tool_name="bash")
        # Cache hit means the speculative executor already served the
        # result; we don't need to resolve the tool object either.
        assert out["proceed"] is False
        assert out["resolved_tool"] is None
        assert reg.calls == []  # registry untouched

    def test_proceed_false_passes_through(self) -> None:
        from llm_code.engine.components.deferred_tool_resolver import (
            DeferredToolResolverComponent,
        )

        reg = _FakeRegistry({"bash": _FakeTool("bash")})
        comp = DeferredToolResolverComponent(reg)
        out = comp.run(proceed=False, cache_hit=False, tool_name="bash")
        assert out["proceed"] is False
        assert reg.calls == []

    def test_cache_reuses_prior_resolution(self) -> None:
        from llm_code.engine.components.deferred_tool_resolver import (
            DeferredToolResolverComponent,
        )

        reg = _FakeRegistry({"bash": _FakeTool("bash")})
        comp = DeferredToolResolverComponent(reg)
        comp.run(proceed=True, cache_hit=False, tool_name="bash")
        comp.run(proceed=True, cache_hit=False, tool_name="bash")
        # Registry hit only on the first resolution.
        assert reg.calls == ["bash"]

    def test_cache_expires_oldest_when_full(self) -> None:
        from llm_code.engine.components.deferred_tool_resolver import (
            DeferredToolResolverComponent,
        )

        tools = {f"t{i}": _FakeTool(f"t{i}") for i in range(5)}
        reg = _FakeRegistry(tools)
        comp = DeferredToolResolverComponent(reg, cache_size=2)
        comp.run(proceed=True, cache_hit=False, tool_name="t0")
        comp.run(proceed=True, cache_hit=False, tool_name="t1")
        comp.run(proceed=True, cache_hit=False, tool_name="t2")
        # t0 is now evicted; re-resolving should hit the registry again.
        comp.run(proceed=True, cache_hit=False, tool_name="t0")
        assert reg.calls.count("t0") == 2

    def test_lru_touch_on_hit(self) -> None:
        from llm_code.engine.components.deferred_tool_resolver import (
            DeferredToolResolverComponent,
        )

        tools = {f"t{i}": _FakeTool(f"t{i}") for i in range(3)}
        reg = _FakeRegistry(tools)
        comp = DeferredToolResolverComponent(reg, cache_size=2)
        comp.run(proceed=True, cache_hit=False, tool_name="t0")
        comp.run(proceed=True, cache_hit=False, tool_name="t1")
        # Touch t0 so t1 becomes the eviction target.
        comp.run(proceed=True, cache_hit=False, tool_name="t0")
        comp.run(proceed=True, cache_hit=False, tool_name="t2")
        # t0 should still be cached.
        comp.run(proceed=True, cache_hit=False, tool_name="t0")
        assert reg.calls.count("t0") == 1

    def test_stats_reports_counters(self) -> None:
        from llm_code.engine.components.deferred_tool_resolver import (
            DeferredToolResolverComponent,
        )

        reg = _FakeRegistry({"bash": _FakeTool("bash")})
        comp = DeferredToolResolverComponent(reg)
        comp.run(proceed=True, cache_hit=False, tool_name="bash")
        comp.run(proceed=True, cache_hit=False, tool_name="bash")
        stats = comp.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["size"] == 1

    def test_clear_empties_cache(self) -> None:
        from llm_code.engine.components.deferred_tool_resolver import (
            DeferredToolResolverComponent,
        )

        reg = _FakeRegistry({"bash": _FakeTool("bash")})
        comp = DeferredToolResolverComponent(reg)
        comp.run(proceed=True, cache_hit=False, tool_name="bash")
        comp.clear()
        comp.run(proceed=True, cache_hit=False, tool_name="bash")
        assert reg.calls == ["bash", "bash"]


class TestDeferredToolResolverInPipeline:
    def test_wires_after_speculative(self) -> None:
        from llm_code.engine.components.deferred_tool_resolver import (
            DeferredToolResolverComponent,
        )
        from llm_code.engine.components.speculative_executor import (
            SpeculativeExecutorComponent,
        )
        from llm_code.engine.pipeline import Pipeline

        reg = _FakeRegistry({"bash": _FakeTool("bash")})
        p = Pipeline()
        p.add_component("spec", SpeculativeExecutorComponent())
        p.add_component("resolver", DeferredToolResolverComponent(reg))
        p.connect("spec.cache_hit", "resolver.cache_hit")
        p.connect("spec.proceed", "resolver.proceed")
        # tool_name remains an entry socket for the Pipeline caller.
        assert "tool_name" in p.inputs()["resolver"]

    def test_pipeline_run_resolves_on_miss(self) -> None:
        from llm_code.engine.components.deferred_tool_resolver import (
            DeferredToolResolverComponent,
        )
        from llm_code.engine.pipeline import Pipeline

        tool = _FakeTool("bash")
        reg = _FakeRegistry({"bash": tool})
        p = Pipeline()
        p.add_component("resolver", DeferredToolResolverComponent(reg))
        out = p.run({
            "resolver": {
                "proceed": True,
                "cache_hit": False,
                "tool_name": "bash",
            },
        })
        assert out["resolver"]["resolved_tool"] is tool


class TestDeferredToolResolverValidation:
    def test_cache_size_must_be_positive(self) -> None:
        from llm_code.engine.components.deferred_tool_resolver import (
            DeferredToolResolverComponent,
        )

        reg = _FakeRegistry({})
        with pytest.raises(ValueError):
            DeferredToolResolverComponent(reg, cache_size=-1)

    def test_cache_size_zero_disables_caching(self) -> None:
        from llm_code.engine.components.deferred_tool_resolver import (
            DeferredToolResolverComponent,
        )

        reg = _FakeRegistry({"bash": _FakeTool("bash")})
        comp = DeferredToolResolverComponent(reg, cache_size=0)
        comp.run(proceed=True, cache_hit=False, tool_name="bash")
        comp.run(proceed=True, cache_hit=False, tool_name="bash")
        assert reg.calls.count("bash") == 2
