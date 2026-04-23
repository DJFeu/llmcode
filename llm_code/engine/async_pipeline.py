"""AsyncPipeline — topological execution with bounded parallel concurrency.

Inherits from :class:`~llm_code.engine.pipeline.Pipeline` to reuse all
wiring, validation, and introspection. Overrides execution with
``run_async`` that:

1. Computes topological *levels* — sets of components whose upstream
   deps are all satisfied at the same point in the order. Components
   in the same level can safely run in parallel.
2. Within each level, buckets components by ``concurrency_group`` (a
   class attribute; defaults to :data:`DEFAULT_GROUP`). Each bucket is
   dispatched through :func:`run_group_parallel` with a semaphore-bounded
   fan-out so pipeline concurrency cannot blow past
   :data:`MAX_GROUP_PARALLELISM`.

Sync callers can still use the base :meth:`Pipeline.run`. A ``run_via_async``
shim is also provided so parity tests can drive the async path from a
sync context — it's a private-looking helper, not a public API.

Spec: docs/superpowers/specs/2026-04-21-llm-code-v12-haystack-borrow-design.md §5.5
Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-async-pipeline.md Task 5.3
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

from llm_code.engine.concurrency import DEFAULT_GROUP, run_group_parallel
from llm_code.engine.graph import CyclicGraphError
from llm_code.engine.pipeline import Pipeline

logger = logging.getLogger(__name__)


class AsyncPipeline(Pipeline):
    """Pipeline that executes via ``async def run_async()``.

    Usage::

        pipe = AsyncPipeline()
        pipe.add_component("a", MyCompA())
        pipe.add_component("b", MyCompB())
        pipe.connect("a.out", "b.x")
        result = await pipe.run_async({"a": {"seed": 1}})

    The sync :meth:`Pipeline.run` remains available — it executes
    components one at a time with no concurrency and no bridging —
    useful for hot-path tests where thread-pool overhead would skew
    timings.
    """

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def run_async(
        self,
        inputs: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Execute the pipeline asynchronously, level-by-level.

        Components at the same topological depth whose
        ``concurrency_group`` attribute matches are run in parallel
        through :func:`run_group_parallel`. Components in different
        groups at the same level still overlap, but each group bounds
        its own fan-out — different groups do not share a semaphore.

        Raises:
            RuntimeError: wrapping :class:`CyclicGraphError` on cycles.
            Any exception raised by a component propagates after all
            siblings in the same ``gather`` have been drained / cancelled.
        """
        try:
            order = self._graph.topological_sort()
        except CyclicGraphError as exc:
            raise RuntimeError(f"pipeline has a cycle: {exc}") from exc

        # Observability: open a pipeline-scoped span (mirrors sync path).
        try:
            from llm_code.engine.tracing import pipeline_span as _pipeline_span
        except Exception:  # pragma: no cover - defensive
            _pipeline_span = None

        if _pipeline_span is None:
            return await self._run_async_inner(order, inputs)
        with _pipeline_span(type(self).__name__):
            return await self._run_async_inner(order, inputs)

    async def _run_async_inner(
        self,
        order: list[str],
        inputs: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Topological body; split out so the pipeline_span context wraps it."""
        outputs: dict[str, dict[str, Any]] = {}
        levels = self._compute_levels(order)
        for level in levels:
            # Bucket the same-level components by concurrency_group so
            # MAX_GROUP_PARALLELISM is applied per-group, not per-level.
            grouped: dict[str, list[str]] = defaultdict(list)
            for comp_name in level:
                comp = self._components[comp_name]
                group = str(getattr(comp, "concurrency_group", DEFAULT_GROUP))
                grouped[group].append(comp_name)

            # Each group drains in parallel; groups themselves overlap
            # via an outer gather() so independent fast + slow groups
            # don't serialise.
            async def _run_group(members: list[str]) -> list[tuple[str, dict]]:
                coros = [self._run_one_async(m, inputs, outputs) for m in members]
                results = await run_group_parallel(coros)
                return list(zip(members, results))

            group_coros = [_run_group(members) for members in grouped.values()]
            all_group_results = await asyncio.gather(*group_coros)
            for group_result in all_group_results:
                for name, result in group_result:
                    outputs[name] = result if result is not None else {}
        return outputs

    async def _run_one_async(
        self,
        comp_name: str,
        entry_inputs: dict[str, dict[str, Any]],
        outputs: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Resolve inputs, then ``await`` the component's ``run_async``.

        All components expose ``run_async`` — either authored directly
        or auto-bridged by :mod:`async_component`. Sync-only components
        therefore execute on a thread via ``asyncio.to_thread`` inside
        the bridge, which keeps the loop unblocked.
        """
        comp_inputs = self._resolve_inputs(comp_name, entry_inputs, outputs)
        comp = self._components[comp_name]
        try:
            result = await comp.run_async(**comp_inputs)
        except Exception:
            logger.exception("component %s failed (async)", comp_name)
            raise
        return result if result is not None else {}

    # ------------------------------------------------------------------
    # Level computation
    # ------------------------------------------------------------------

    def _compute_levels(self, order: list[str]) -> list[list[str]]:
        """Partition topological ``order`` into level-sets.

        A component's *level* is the length of the longest path from
        any root to that component. Two components at the same level
        have disjoint dependency chains (relative to each other) and
        can therefore run concurrently without data hazards.

        Args:
            order: Topologically-sorted node list.

        Returns:
            List of levels; each level is a list of component names.
            Levels are returned in execution order (shallowest first).
        """
        depth: dict[str, int] = {}
        for name in order:
            preds = self._graph._reverse.get(name, set())
            depth[name] = 0 if not preds else max(depth[p] for p in preds) + 1
        by_level: dict[int, list[str]] = defaultdict(list)
        for name, d in depth.items():
            by_level[d].append(name)
        # Stable within-level ordering — alphabetical — so log output
        # and parity tests are deterministic even though group execution
        # is concurrent.
        return [sorted(by_level[d]) for d in sorted(by_level)]

    # ------------------------------------------------------------------
    # Convenience factory
    # ------------------------------------------------------------------

    @classmethod
    def _from(cls, base: Pipeline) -> "AsyncPipeline":
        """Adopt a populated :class:`Pipeline` as an :class:`AsyncPipeline`.

        Parity helper used in tests: takes the graph + components dict
        + connections dict wholesale so a sync pipeline can be driven
        through the async path without re-wiring.
        """
        new = cls()
        new._graph = base._graph
        new._components = base._components
        new._connections = base._connections
        return new


def run_via_async(pipeline: Pipeline, inputs: dict[str, dict[str, Any]]) -> dict:
    """Drive ``pipeline`` through the async engine from a sync context.

    Convenience for parity tests. Not intended for production code —
    production runtime should either call :meth:`Pipeline.run` (sync)
    or ``await AsyncPipeline.run_async`` (async), not this bridge.

    Raises:
        RuntimeError: if called from within a running event loop.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(AsyncPipeline._from(pipeline).run_async(inputs))
    raise RuntimeError(
        "run_via_async() cannot be called from inside a running loop — "
        "await AsyncPipeline.run_async() directly."
    )


__all__ = ["AsyncPipeline", "run_via_async"]
