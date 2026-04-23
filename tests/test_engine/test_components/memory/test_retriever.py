"""Tests for v12 M7 Task 7.3 — RetrieverComponent.

Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-memory-components.md
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone


from llm_code.engine.component import (
    get_input_sockets,
    get_output_sockets,
    is_component,
)
from llm_code.engine.components.memory.retriever import RetrieverComponent
from llm_code.engine.components.memory.schema import (
    MemoryEntry,
    MemoryScope,
    Retrieval,
)
from llm_code.memory.layer import InMemoryMemoryLayer

_FROZEN_UTC = datetime(2026, 4, 21, 12, 0, 0, tzinfo=timezone.utc)


def _entry(
    entry_id: str,
    text: str,
    *,
    scope: MemoryScope = MemoryScope.PROJECT,
    embedding: tuple[float, ...] | None = (1.0, 0.0, 0.0),
    source_tool: str | None = None,
    created_at: datetime | None = None,
    metadata: dict | None = None,
) -> MemoryEntry:
    return MemoryEntry(
        id=entry_id,
        text=text,
        scope=scope,
        created_at=created_at or _FROZEN_UTC,
        embedding=embedding,
        source_tool=source_tool,
        metadata=metadata or {},
    )


def _seed_layer(entries: list[MemoryEntry]) -> InMemoryMemoryLayer:
    layer = InMemoryMemoryLayer()
    for e in entries:
        layer.write(e)
    return layer


class TestRetrieverShape:
    def test_marked_as_component(self) -> None:
        comp = RetrieverComponent(InMemoryMemoryLayer())
        assert is_component(comp)

    def test_declares_inputs(self) -> None:
        inputs = get_input_sockets(RetrieverComponent)
        assert set(inputs) == {
            "embedding",
            "query",
            "scope",
            "scope_filters",
            "top_k",
        }

    def test_declares_outputs(self) -> None:
        outputs = get_output_sockets(RetrieverComponent)
        assert set(outputs) == {"entries", "scores", "retrieval"}

    def test_concurrency_group(self) -> None:
        assert RetrieverComponent.concurrency_group == "io_bound"

    def test_layer_property(self) -> None:
        layer = InMemoryMemoryLayer()
        comp = RetrieverComponent(layer)
        assert comp.layer is layer


class TestRetrieverRun:
    def test_empty_layer_returns_empty_tuples(self) -> None:
        comp = RetrieverComponent(InMemoryMemoryLayer())
        out = comp.run(embedding=(1.0, 0.0, 0.0), query="anything")
        assert out["entries"] == ()
        assert out["scores"] == ()

    def test_retrieval_object_packaged(self) -> None:
        comp = RetrieverComponent(InMemoryMemoryLayer())
        out = comp.run(embedding=(1.0, 0.0, 0.0), query="q")
        assert isinstance(out["retrieval"], Retrieval)
        assert out["retrieval"].query == "q"
        assert out["retrieval"].query_embedding == (1.0, 0.0, 0.0)

    def test_parallel_array_invariant(self) -> None:
        layer = _seed_layer([
            _entry("e1", "alpha", embedding=(1.0, 0.0, 0.0)),
            _entry("e2", "beta", embedding=(0.9, 0.1, 0.0)),
        ])
        comp = RetrieverComponent(layer)
        out = comp.run(embedding=(1.0, 0.0, 0.0), query="q")
        assert len(out["entries"]) == len(out["scores"])

    def test_top_k_respected(self) -> None:
        layer = _seed_layer([
            _entry(f"e{i}", f"text {i}", embedding=(1.0 - i * 0.01, 0.0, 0.0))
            for i in range(10)
        ])
        comp = RetrieverComponent(layer, default_top_k=3)
        out = comp.run(embedding=(1.0, 0.0, 0.0), query="q")
        assert len(out["entries"]) == 3

    def test_top_k_override(self) -> None:
        layer = _seed_layer([
            _entry(f"e{i}", f"text {i}", embedding=(1.0 - i * 0.01, 0.0, 0.0))
            for i in range(10)
        ])
        comp = RetrieverComponent(layer, default_top_k=3)
        out = comp.run(embedding=(1.0, 0.0, 0.0), query="q", top_k=5)
        assert len(out["entries"]) == 5

    def test_project_scope_sees_global(self) -> None:
        layer = _seed_layer([
            _entry("p1", "project entry", scope=MemoryScope.PROJECT,
                   embedding=(1.0, 0.0, 0.0)),
            _entry("g1", "global entry", scope=MemoryScope.GLOBAL,
                   embedding=(0.9, 0.1, 0.0)),
            _entry("s1", "session entry", scope=MemoryScope.SESSION,
                   embedding=(0.5, 0.5, 0.0)),
        ])
        comp = RetrieverComponent(layer, default_scope=MemoryScope.PROJECT)
        out = comp.run(embedding=(1.0, 0.0, 0.0), query="q", top_k=10)
        ids = {e.id for e in out["entries"]}
        assert "p1" in ids
        assert "g1" in ids  # GLOBAL is visible from PROJECT
        assert "s1" not in ids  # SESSION is private

    def test_session_scope_isolation(self) -> None:
        layer = _seed_layer([
            _entry("p1", "project", scope=MemoryScope.PROJECT,
                   embedding=(1.0, 0.0, 0.0)),
            _entry("s1", "session", scope=MemoryScope.SESSION,
                   embedding=(1.0, 0.0, 0.0)),
        ])
        comp = RetrieverComponent(layer, default_scope=MemoryScope.SESSION)
        out = comp.run(embedding=(1.0, 0.0, 0.0), query="q", top_k=10)
        ids = {e.id for e in out["entries"]}
        assert ids == {"s1"}

    def test_scope_override(self) -> None:
        layer = _seed_layer([
            _entry("s1", "session", scope=MemoryScope.SESSION,
                   embedding=(1.0, 0.0, 0.0)),
        ])
        comp = RetrieverComponent(layer, default_scope=MemoryScope.PROJECT)
        out = comp.run(
            embedding=(1.0, 0.0, 0.0),
            query="q",
            scope=MemoryScope.SESSION,
        )
        assert {e.id for e in out["entries"]} == {"s1"}

    def test_filters_forwarded_source_tool(self) -> None:
        layer = _seed_layer([
            _entry("e1", "bash hit", source_tool="bash",
                   embedding=(1.0, 0.0, 0.0)),
            _entry("e2", "read hit", source_tool="read_file",
                   embedding=(1.0, 0.0, 0.0)),
        ])
        comp = RetrieverComponent(layer)
        out = comp.run(
            embedding=(1.0, 0.0, 0.0),
            query="q",
            scope_filters={"source_tool": "bash"},
        )
        ids = {e.id for e in out["entries"]}
        assert ids == {"e1"}

    def test_ordering_by_score_desc(self) -> None:
        layer = _seed_layer([
            _entry("low", "a", embedding=(0.1, 1.0, 0.0)),
            _entry("high", "b", embedding=(1.0, 0.0, 0.0)),
            _entry("mid", "c", embedding=(0.7, 0.3, 0.0)),
        ])
        comp = RetrieverComponent(layer, default_top_k=10)
        out = comp.run(embedding=(1.0, 0.0, 0.0), query="q")
        # Scores must be non-increasing.
        for a, b in zip(out["scores"], out["scores"][1:]):
            assert a >= b


class TestRetrieverAsync:
    def test_run_async_matches_sync(self) -> None:
        layer = _seed_layer([
            _entry("e1", "hello", embedding=(1.0, 0.0, 0.0)),
            _entry("e2", "world", embedding=(0.8, 0.2, 0.0)),
        ])
        comp = RetrieverComponent(layer)
        sync_out = comp.run(embedding=(1.0, 0.0, 0.0), query="q")
        async_out = asyncio.run(
            comp.run_async(embedding=(1.0, 0.0, 0.0), query="q")
        )
        assert [e.id for e in sync_out["entries"]] == [e.id for e in async_out["entries"]]

    def test_async_uses_to_thread_bridge(self) -> None:
        layer = _seed_layer([
            _entry("e1", "x", embedding=(1.0, 0.0, 0.0)),
        ])
        comp = RetrieverComponent(layer)
        out = asyncio.run(comp.run_async(embedding=(1.0, 0.0, 0.0), query="q"))
        assert out["entries"][0].id == "e1"
