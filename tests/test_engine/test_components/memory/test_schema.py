"""Tests for v12 M7 Task 7.1 — memory schema shared types.

Validates:
- ``MemoryScope`` is re-exported from ``llm_code.engine.state`` (single
  definition, no duplication).
- ``MemoryEntry`` is a frozen dataclass with the documented field shape.
- ``Retrieval`` is a frozen dataclass that enforces the parallel-array
  invariant ``len(entries) == len(scores)`` via ``__post_init__``.

Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-memory-components.md
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest


# A deterministic UTC datetime used across tests to avoid flakiness.
FROZEN_UTC = datetime(2026, 4, 21, 12, 0, 0, tzinfo=timezone.utc)


class TestMemoryScopeReExport:
    def test_memory_scope_reexported_same_object(self) -> None:
        from llm_code.engine.components.memory import schema as schema_mod
        from llm_code.engine.state import MemoryScope as StateScope

        assert schema_mod.MemoryScope is StateScope

    def test_memory_scope_members(self) -> None:
        from llm_code.engine.components.memory.schema import MemoryScope

        assert MemoryScope.SESSION.value == "session"
        assert MemoryScope.PROJECT.value == "project"
        assert MemoryScope.GLOBAL.value == "global"


class TestMemoryEntryShape:
    def test_is_frozen_dataclass(self) -> None:
        from llm_code.engine.components.memory.schema import MemoryEntry

        assert dataclasses.is_dataclass(MemoryEntry)
        field_names = {f.name for f in dataclasses.fields(MemoryEntry)}
        assert field_names == {
            "id",
            "text",
            "embedding",
            "scope",
            "created_at",
            "source_tool",
            "metadata",
        }

    def test_frozen_setattr_raises(self) -> None:
        from llm_code.engine.components.memory.schema import (
            MemoryEntry,
            MemoryScope,
        )

        entry = MemoryEntry(
            id="e1",
            text="hello",
            embedding=(0.1, 0.2, 0.3),
            scope=MemoryScope.PROJECT,
            created_at=FROZEN_UTC,
            source_tool="bash",
            metadata={},
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            entry.text = "mutated"  # type: ignore[misc]

    def test_construction_with_all_fields(self) -> None:
        from llm_code.engine.components.memory.schema import (
            MemoryEntry,
            MemoryScope,
        )

        entry = MemoryEntry(
            id="abc",
            text="what a useful note",
            embedding=(0.1, 0.2, 0.3),
            scope=MemoryScope.GLOBAL,
            created_at=FROZEN_UTC,
            source_tool="read",
            metadata={"k": "v", "n": 1},
        )
        assert entry.id == "abc"
        assert entry.text == "what a useful note"
        assert entry.embedding == (0.1, 0.2, 0.3)
        assert entry.scope is MemoryScope.GLOBAL
        assert entry.created_at is FROZEN_UTC
        assert entry.source_tool == "read"
        assert entry.metadata == {"k": "v", "n": 1}

    def test_defaults_embedding_none_source_none_metadata_empty(self) -> None:
        from llm_code.engine.components.memory.schema import (
            MemoryEntry,
            MemoryScope,
        )

        entry = MemoryEntry(
            id="e2",
            text="t",
            scope=MemoryScope.SESSION,
            created_at=FROZEN_UTC,
        )
        assert entry.embedding is None
        assert entry.source_tool is None
        assert entry.metadata == {}

    def test_defaults_metadata_is_independent_per_instance(self) -> None:
        """Guard against shared-mutable-default bugs."""
        from llm_code.engine.components.memory.schema import (
            MemoryEntry,
            MemoryScope,
        )

        a = MemoryEntry(
            id="1", text="a", scope=MemoryScope.PROJECT, created_at=FROZEN_UTC,
        )
        b = MemoryEntry(
            id="2", text="b", scope=MemoryScope.PROJECT, created_at=FROZEN_UTC,
        )
        assert a.metadata is not b.metadata

    def test_equality_compares_all_fields(self) -> None:
        from llm_code.engine.components.memory.schema import (
            MemoryEntry,
            MemoryScope,
        )

        e1 = MemoryEntry(
            id="x",
            text="t",
            embedding=(1.0,),
            scope=MemoryScope.PROJECT,
            created_at=FROZEN_UTC,
            source_tool="bash",
            metadata={"a": 1},
        )
        e2 = MemoryEntry(
            id="x",
            text="t",
            embedding=(1.0,),
            scope=MemoryScope.PROJECT,
            created_at=FROZEN_UTC,
            source_tool="bash",
            metadata={"a": 1},
        )
        assert e1 == e2


class TestRetrievalShape:
    def test_is_frozen_dataclass(self) -> None:
        from llm_code.engine.components.memory.schema import Retrieval

        assert dataclasses.is_dataclass(Retrieval)
        field_names = {f.name for f in dataclasses.fields(Retrieval)}
        assert field_names == {
            "entries",
            "scores",
            "query",
            "query_embedding",
            "retrieved_at",
        }

    def test_frozen_setattr_raises(self) -> None:
        from llm_code.engine.components.memory.schema import Retrieval

        r = Retrieval(
            entries=(),
            scores=(),
            query="q",
            query_embedding=(0.1,),
            retrieved_at=FROZEN_UTC,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.query = "other"  # type: ignore[misc]

    def test_parallel_array_invariant_valid(self) -> None:
        from llm_code.engine.components.memory.schema import (
            MemoryEntry,
            MemoryScope,
            Retrieval,
        )

        entry = MemoryEntry(
            id="1", text="t", scope=MemoryScope.PROJECT, created_at=FROZEN_UTC,
        )
        r = Retrieval(
            entries=(entry,),
            scores=(0.9,),
            query="q",
            query_embedding=(0.1, 0.2),
            retrieved_at=FROZEN_UTC,
        )
        assert len(r.entries) == len(r.scores)

    def test_parallel_array_invariant_violation_raises(self) -> None:
        from llm_code.engine.components.memory.schema import (
            MemoryEntry,
            MemoryScope,
            Retrieval,
        )

        entry = MemoryEntry(
            id="1", text="t", scope=MemoryScope.PROJECT, created_at=FROZEN_UTC,
        )
        with pytest.raises(ValueError):
            Retrieval(
                entries=(entry,),
                scores=(0.9, 0.8),  # two scores, one entry
                query="q",
                query_embedding=(0.1,),
                retrieved_at=FROZEN_UTC,
            )

    def test_empty_entries_and_scores_valid(self) -> None:
        from llm_code.engine.components.memory.schema import Retrieval

        r = Retrieval(
            entries=(),
            scores=(),
            query="q",
            query_embedding=(0.0,),
            retrieved_at=FROZEN_UTC,
        )
        assert r.entries == ()
        assert r.scores == ()

    def test_entries_and_scores_are_tuples(self) -> None:
        """Parallel arrays are stored as tuples (immutable)."""
        from llm_code.engine.components.memory.schema import (
            MemoryEntry,
            MemoryScope,
            Retrieval,
        )

        entry = MemoryEntry(
            id="1", text="t", scope=MemoryScope.PROJECT, created_at=FROZEN_UTC,
        )
        r = Retrieval(
            entries=(entry,),
            scores=(0.5,),
            query="q",
            query_embedding=(0.1,),
            retrieved_at=FROZEN_UTC,
        )
        assert isinstance(r.entries, tuple)
        assert isinstance(r.scores, tuple)
