"""Pipeline wiring helper for memory Components (v12 M7 Task 7.7).

Exposed so ``engine/default_pipeline.py`` (M2) — and integration tests —
can compose the five memory Components without copy-pasting the six
``Pipeline.connect`` calls. Usage::

    from llm_code.engine.pipeline import Pipeline
    from llm_code.engine.components.memory.wire import wire_memory_components

    pipeline = Pipeline()
    wire_memory_components(pipeline, memory_config, layer)

The wiring is opt-in: when ``memory_config.enabled`` is ``False`` the
helper returns without touching the pipeline.

Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-memory-components.md
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from llm_code.engine.components.memory.context import MemoryContextComponent
from llm_code.engine.components.memory.embedder import (
    EmbedderComponent,
    build_embedder_from_config,
)
from llm_code.engine.components.memory.reranker import build_reranker_from_config
from llm_code.engine.components.memory.retriever import RetrieverComponent
from llm_code.engine.components.memory.schema import MemoryScope
from llm_code.engine.components.memory.writer import MemoryWriterComponent
from llm_code.engine.pipeline import Pipeline
from llm_code.memory.layer import InMemoryMemoryLayer, MemoryLayer

__all__ = ["MemoryWiring", "wire_memory_components"]


@dataclass(frozen=True)
class MemoryWiring:
    """Result of :func:`wire_memory_components`.

    A bundle of the instances that were added so callers can reach back
    in for dynamic reconfiguration (e.g. rotating the HIDA index during
    a long-lived session) without re-traversing the Pipeline graph.
    """

    embedder: EmbedderComponent | None
    retriever: RetrieverComponent | None
    reranker: Any | None
    writer: MemoryWriterComponent | None
    context: MemoryContextComponent | None
    layer: MemoryLayer | None
    enabled: bool


def wire_memory_components(
    pipeline: Pipeline,
    config: Any,
    layer: MemoryLayer | None = None,
    *,
    llm_provider: Any | None = None,
) -> MemoryWiring:
    """Add the five memory Components to ``pipeline`` and connect them.

    When ``config.enabled`` is ``False`` this function is a no-op and
    returns a :class:`MemoryWiring` with every handle set to ``None``
    plus ``enabled=False`` — preserving the "memory-disabled
    byte-identical to pre-M7" contract (parity test lives under
    ``tests/test_engine/parity/``).

    Args:
        pipeline: The :class:`Pipeline` to mutate in place.
        config: A :class:`MemoryConfig` (or compatible duck-type).
        layer: Optional :class:`MemoryLayer`. Defaults to an
            :class:`InMemoryMemoryLayer` so callers can compose in
            tests without touching disk.
        llm_provider: Optional LLM client for :class:`LLMReranker`;
            ignored when the configured reranker is not ``llm``.

    Returns:
        :class:`MemoryWiring` bundle for introspection / dynamic
        reconfiguration.
    """
    if not bool(getattr(config, "enabled", True)):
        return MemoryWiring(None, None, None, None, None, None, enabled=False)

    backend = layer if layer is not None else InMemoryMemoryLayer()

    embedder = build_embedder_from_config(config)
    default_scope_name = str(getattr(config, "default_scope", "project")) or "project"
    try:
        default_scope = MemoryScope(default_scope_name)
    except ValueError:
        default_scope = MemoryScope.PROJECT

    retriever = RetrieverComponent(
        backend,
        default_top_k=int(getattr(config, "retrieve_top_k", 20) or 20),
        default_scope=default_scope,
    )
    reranker = build_reranker_from_config(config, llm_provider=llm_provider)
    writer = MemoryWriterComponent(
        backend,
        embedder,
        remember_filter=getattr(config, "remember_filter", "always") or "always",
        max_chars=int(getattr(config, "max_context_chars", 4000) or 4000),
        default_scope=default_scope,
    )
    context = MemoryContextComponent(
        template=str(getattr(config, "context_template", "default") or "default"),
        max_chars=int(getattr(config, "max_context_chars", 4000) or 4000),
    )

    pipeline.add_component("embedder", embedder)
    pipeline.add_component("retriever", retriever)
    pipeline.add_component("reranker", reranker)
    pipeline.add_component("memory_context", context)
    pipeline.add_component("memory_writer", writer)

    # Pre-prompt chain: Embedder → Retriever → Reranker → MemoryContext
    pipeline.connect("embedder.embedding", "retriever.embedding")
    pipeline.connect("retriever.entries", "reranker.candidates")
    pipeline.connect("retriever.scores", "reranker.scores")
    pipeline.connect("reranker.entries", "memory_context.entries")

    return MemoryWiring(
        embedder=embedder,
        retriever=retriever,
        reranker=reranker,
        writer=writer,
        context=context,
        layer=backend,
        enabled=True,
    )
