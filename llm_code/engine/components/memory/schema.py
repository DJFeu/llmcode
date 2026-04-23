"""Shared memory-component types (v12 M7 Task 7.1).

Single source of truth for the memory data model passed between
``Embedder → Retriever → Reranker → MemoryContext → PromptAssembler``.

``MemoryScope`` is re-exported from :mod:`llm_code.engine.state` so there
is exactly one enum definition; ``AgentLoopConfig`` / ``HayhooksConfig``
already depend on the scaffolding copy (see ``engine/state.py`` module
docstring).

Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-memory-components.md
Spec: docs/superpowers/specs/2026-04-21-llm-code-v12-haystack-borrow-design.md §5.7
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from llm_code.engine.state import MemoryScope

__all__ = ["MemoryEntry", "MemoryScope", "Retrieval"]


@dataclass(frozen=True)
class MemoryEntry:
    """A single persisted memory item.

    Fields follow the v12 schema (§5.7 design note). The ``embedding`` is
    stored as a ``tuple[float, ...]`` so the entry remains immutable; it
    is ``None`` when the entry predates embedder availability. The
    ``metadata`` dict is mutable internally but the wrapping dataclass is
    frozen so the entry identity cannot be rebound on the consumer side.
    """

    id: str
    text: str
    scope: MemoryScope
    created_at: datetime
    embedding: tuple[float, ...] | None = None
    source_tool: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Retrieval:
    """Output of :class:`RetrieverComponent` — parallel ``entries`` /
    ``scores`` tuples plus the originating query context.

    Invariant: ``len(entries) == len(scores)``. Violations raise
    :class:`ValueError` at construction time via ``__post_init__``.
    """

    entries: tuple[MemoryEntry, ...]
    scores: tuple[float, ...]
    query: str
    query_embedding: tuple[float, ...]
    retrieved_at: datetime

    def __post_init__(self) -> None:
        if len(self.entries) != len(self.scores):
            raise ValueError(
                f"Retrieval parallel-array invariant violated: "
                f"len(entries)={len(self.entries)} != "
                f"len(scores)={len(self.scores)}",
            )
