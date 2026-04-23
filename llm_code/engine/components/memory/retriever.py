"""RetrieverComponent — scope-aware memory retrieval (v12 M7 Task 7.3).

Wraps a :class:`~llm_code.memory.layer.MemoryLayer` as a Pipeline
Component. Inputs: the query embedding (+ optional scope, filters,
top_k override). Outputs: a parallel ``(entries, scores)`` tuple pair
packaged as a :class:`~llm_code.engine.components.memory.schema.Retrieval`.

The async path calls :meth:`MemoryLayer.search_async`, which defaults to
``asyncio.to_thread(self.search, …)`` for sync backends — concrete
backends can override if they have a native async query API.

Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-memory-components.md
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from llm_code.engine.component import component, output_types, state_reads, state_writes
from llm_code.engine.components.memory.filters import visible_scopes_for
from llm_code.engine.components.memory.schema import (
    MemoryEntry,
    MemoryScope,
    Retrieval,
)
from llm_code.engine.tracing import traced_component
from llm_code.memory.layer import MemoryLayer

__all__ = ["RetrieverComponent"]


@traced_component
@component
@output_types(
    entries=tuple,
    scores=tuple,
    retrieval=Retrieval,
)
@state_reads("query_embedding")
@state_writes("memory_entries")
class RetrieverComponent:
    """Fetch the top-k memory entries for a query embedding.

    Args:
        layer: The storage backend. Held by reference so dynamic
            re-indexing is visible to subsequent runs without re-wiring
            the Pipeline.
        default_top_k: Fallback ``top_k`` when the caller does not pass
            one at runtime. Matches
            :attr:`MemoryConfig.retrieve_top_k`.
        default_scope: Fallback scope when the caller does not pass
            one. Matches :attr:`MemoryConfig.default_scope`.

    Inputs:
        embedding: Query embedding (tuple of float).
        query: Original query text — kept so downstream components
            (rerankers, context builders) don't need to thread it
            separately.
        scope: Optional override of ``default_scope``.
        scope_filters: Optional dict forwarded to the backend verbatim.
        top_k: Optional override of ``default_top_k``.

    Outputs:
        entries: Tuple of :class:`MemoryEntry` in rank order.
        scores: Parallel tuple of floats (same length as ``entries``).
        retrieval: Packaged :class:`Retrieval` carrying the query +
            timestamp for downstream consumers.
    """

    concurrency_group = "io_bound"

    def __init__(
        self,
        layer: MemoryLayer,
        *,
        default_top_k: int = 20,
        default_scope: MemoryScope = MemoryScope.PROJECT,
    ) -> None:
        self._layer = layer
        self._default_top_k = int(default_top_k)
        self._default_scope = default_scope

    @property
    def layer(self) -> MemoryLayer:
        return self._layer

    def run(
        self,
        embedding: tuple[float, ...],
        query: str = "",
        scope: MemoryScope | None = None,
        scope_filters: dict[str, Any] | None = None,
        top_k: int | None = None,
    ) -> dict[str, Any]:
        effective_scope = scope if scope is not None else self._default_scope
        effective_top_k = top_k if top_k is not None else self._default_top_k
        entries, scores = self._search(
            embedding,
            effective_top_k,
            effective_scope,
            scope_filters,
        )
        return _package(entries, scores, query, embedding)

    async def run_async(
        self,
        embedding: tuple[float, ...],
        query: str = "",
        scope: MemoryScope | None = None,
        scope_filters: dict[str, Any] | None = None,
        top_k: int | None = None,
    ) -> dict[str, Any]:
        effective_scope = scope if scope is not None else self._default_scope
        effective_top_k = top_k if top_k is not None else self._default_top_k
        entries, scores = await self._search_async(
            embedding,
            effective_top_k,
            effective_scope,
            scope_filters,
        )
        return _package(entries, scores, query, embedding)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _search(
        self,
        embedding: tuple[float, ...],
        top_k: int,
        scope: MemoryScope,
        scope_filters: dict[str, Any] | None,
    ) -> tuple[tuple[MemoryEntry, ...], tuple[float, ...]]:
        """Query each visible scope and merge the results.

        Merging preserves the overall ordering by score so the final
        tuple respects the top_k cap across scopes.
        """
        visible = visible_scopes_for(scope)
        merged: list[tuple[MemoryEntry, float]] = []
        # Over-fetch from each scope so the final top_k is well-defined
        # even when the top-scoring entries are concentrated in one
        # scope.
        for s in visible:
            entries, scores = self._layer.search(
                embedding,
                top_k=top_k,
                scope=s,
                filters=scope_filters,
            )
            merged.extend(zip(entries, scores))
        merged.sort(key=lambda pair: pair[1], reverse=True)
        cut = merged[: max(0, top_k)]
        return tuple(e for e, _ in cut), tuple(s for _, s in cut)

    async def _search_async(
        self,
        embedding: tuple[float, ...],
        top_k: int,
        scope: MemoryScope,
        scope_filters: dict[str, Any] | None,
    ) -> tuple[tuple[MemoryEntry, ...], tuple[float, ...]]:
        visible = visible_scopes_for(scope)
        merged: list[tuple[MemoryEntry, float]] = []
        for s in visible:
            entries, scores = await self._layer.search_async(
                embedding,
                top_k=top_k,
                scope=s,
                filters=scope_filters,
            )
            merged.extend(zip(entries, scores))
        merged.sort(key=lambda pair: pair[1], reverse=True)
        cut = merged[: max(0, top_k)]
        return tuple(e for e, _ in cut), tuple(s for _, s in cut)


# ---------------------------------------------------------------------------
# Packaging helper — shared by sync / async paths.
# ---------------------------------------------------------------------------
def _package(
    entries: tuple[MemoryEntry, ...],
    scores: tuple[float, ...],
    query: str,
    query_embedding: tuple[float, ...],
) -> dict[str, Any]:
    retrieval = Retrieval(
        entries=entries,
        scores=scores,
        query=query,
        query_embedding=tuple(query_embedding),
        retrieved_at=datetime.now(timezone.utc),
    )
    return {
        "entries": entries,
        "scores": scores,
        "retrieval": retrieval,
    }
