"""``MemoryLayer`` storage ABC + in-memory reference implementation.

The Pipeline-side Components (``Retriever``, ``MemoryWriter``) depend on
this abstraction — not on any concrete HIDA index file. Swapping backends
(in-memory, SQLite, a future real HIDA index) is a one-line dependency
injection in :func:`wire_memory_components`.

Design:

- :class:`MemoryLayer` — abstract storage contract. Implementations are
  responsible for persisting entries, reading them back, and running a
  simple similarity search against a query embedding. Async methods
  have default implementations that bridge sync via ``asyncio.to_thread``.
- :class:`InMemoryMemoryLayer` — zero-dependency backend used by tests
  and as the default when no HIDA index path is configured. Uses cosine
  similarity over the stored embedding vectors.

Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-memory-components.md
"""
from __future__ import annotations

import asyncio
import math
from abc import ABC, abstractmethod
from typing import Any

from llm_code.engine.components.memory.schema import MemoryEntry, MemoryScope

__all__ = ["InMemoryMemoryLayer", "MemoryLayer"]


class MemoryLayer(ABC):
    """Abstract contract for memory storage backends.

    Concrete implementations ship either in :mod:`llm_code.memory` (for
    batteries-included defaults) or under ``llm_code/memory/backends/``
    (heavier backends gated behind optional deps).
    """

    # ------------------------------------------------------------------
    # Sync API — every backend must implement these two methods.
    # ------------------------------------------------------------------
    @abstractmethod
    def write(self, entry: MemoryEntry) -> None:
        """Persist ``entry`` in the backend."""

    @abstractmethod
    def search(
        self,
        embedding: tuple[float, ...],
        *,
        top_k: int = 20,
        scope: MemoryScope | None = None,
        filters: dict[str, Any] | None = None,
    ) -> tuple[tuple[MemoryEntry, ...], tuple[float, ...]]:
        """Return the top-k entries matching ``embedding`` (plus scores).

        ``filters`` is forwarded verbatim to the backend — e.g.
        ``{"source_tool": "bash", "created_after": "2026-04-01"}``.
        Backends translate the dict into their native query syntax.
        """

    # ------------------------------------------------------------------
    # Async bridge — sync backends inherit these unchanged.
    # ------------------------------------------------------------------
    async def write_async(self, entry: MemoryEntry) -> None:
        await asyncio.to_thread(self.write, entry)

    async def search_async(
        self,
        embedding: tuple[float, ...],
        *,
        top_k: int = 20,
        scope: MemoryScope | None = None,
        filters: dict[str, Any] | None = None,
    ) -> tuple[tuple[MemoryEntry, ...], tuple[float, ...]]:
        return await asyncio.to_thread(
            self.search,
            embedding,
            top_k=top_k,
            scope=scope,
            filters=filters,
        )


class InMemoryMemoryLayer(MemoryLayer):
    """Zero-dependency in-memory storage — cosine-similarity search.

    The implementation is intentionally tiny: entries live in a list and
    ``search`` does an O(n·d) scan. Fine for tests and small sessions;
    for anything larger, swap in a real HIDA index (out of M7 scope).
    """

    def __init__(self) -> None:
        self._entries: list[MemoryEntry] = []

    # ------------------------------------------------------------------
    # Introspection helpers (non-ABC) — used by tests / parity runners.
    # ------------------------------------------------------------------
    def __len__(self) -> int:
        return len(self._entries)

    def all_entries(self) -> tuple[MemoryEntry, ...]:
        return tuple(self._entries)

    # ------------------------------------------------------------------
    # ABC surface
    # ------------------------------------------------------------------
    def write(self, entry: MemoryEntry) -> None:
        self._entries.append(entry)

    def search(
        self,
        embedding: tuple[float, ...],
        *,
        top_k: int = 20,
        scope: MemoryScope | None = None,
        filters: dict[str, Any] | None = None,
    ) -> tuple[tuple[MemoryEntry, ...], tuple[float, ...]]:
        filtered = [
            e for e in self._entries
            if _passes_scope(e, scope) and _passes_filters(e, filters)
        ]
        scored: list[tuple[MemoryEntry, float]] = []
        for e in filtered:
            if e.embedding is None:
                continue
            scored.append((e, _cosine(e.embedding, embedding)))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        cut = scored[:max(0, int(top_k))]
        entries = tuple(e for e, _ in cut)
        scores = tuple(s for _, s in cut)
        return entries, scores


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _passes_scope(entry: MemoryEntry, scope: MemoryScope | None) -> bool:
    """Return True iff ``entry`` is visible in ``scope``.

    ``scope=None`` means "all scopes". Otherwise an exact match is
    required — scope semantics are opaque to the storage layer; the
    enforcement rule of "GLOBAL entries are visible from PROJECT scope"
    (etc.) lives in :mod:`filters` so tests can assert on the policy.
    """
    if scope is None:
        return True
    return entry.scope == scope


def _passes_filters(entry: MemoryEntry, filters: dict[str, Any] | None) -> bool:
    """Apply a small subset of the documented filter syntax.

    Recognised keys:

    - ``source_tool``: exact equality against ``entry.source_tool``.
    - ``created_after``: ISO datetime string; entries older than this
      are dropped.
    - ``metadata.<key>``: dotted path; equality match against
      ``entry.metadata.get(key)``.

    Unknown keys are ignored so downstream backends that understand
    more can layer on top; ignoring is safer than raising because a
    stale caller should never crash the pipeline.
    """
    if not filters:
        return True
    import datetime as _dt

    for key, expected in filters.items():
        if key == "source_tool":
            if entry.source_tool != expected:
                return False
        elif key == "created_after":
            try:
                bound = _dt.datetime.fromisoformat(str(expected))
            except ValueError:
                continue
            if entry.created_at < bound:
                return False
        elif key.startswith("metadata."):
            inner = key.split(".", 1)[1]
            if entry.metadata.get(inner) != expected:
                return False
    return True


def _cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Cosine similarity; returns 0 when either vector is zero."""
    if not a or not b:
        return 0.0
    # Zip truncates silently; guard explicitly so callers know dimension
    # mismatches are a programming error rather than a silent zero.
    if len(a) != len(b):
        raise ValueError(
            f"Cosine similarity dimension mismatch: {len(a)} != {len(b)}"
        )
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
