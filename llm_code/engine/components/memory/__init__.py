"""v12 memory components — M7. Embedder / Retriever / Reranker / Writer / Context.

Folds v10 HIDA into the Pipeline as first-class Components. See plan #7.
"""
from __future__ import annotations

from llm_code.engine.components.memory.context import MemoryContextComponent
from llm_code.engine.components.memory.embedder import (
    DeterministicHashBackend,
    EmbedderComponent,
    EmbeddingBackend,
    build_embedder_from_config,
)
from llm_code.engine.components.memory.filters import (
    apply_filters,
    filter_by_metadata,
    filter_by_scope,
    filter_by_source_tool,
    filter_by_time,
    visible_scopes_for,
)
from llm_code.engine.components.memory.reranker import (
    CrossEncoderReranker,
    LLMReranker,
    NoopReranker,
    RerankerComponent,
    build_reranker_from_config,
)
from llm_code.engine.components.memory.retriever import RetrieverComponent
from llm_code.engine.components.memory.schema import (
    MemoryEntry,
    MemoryScope,
    Retrieval,
)
from llm_code.engine.components.memory.wire import (
    MemoryWiring,
    wire_memory_components,
)
from llm_code.engine.components.memory.writer import (
    MemoryWriterComponent,
    RememberFilter,
    default_should_remember,
    never_should_remember,
    non_read_only_only,
    on_error_only,
    resolve_remember_filter,
)

__all__ = [
    "CrossEncoderReranker",
    "DeterministicHashBackend",
    "EmbedderComponent",
    "EmbeddingBackend",
    "LLMReranker",
    "MemoryContextComponent",
    "MemoryEntry",
    "MemoryScope",
    "MemoryWiring",
    "MemoryWriterComponent",
    "NoopReranker",
    "RememberFilter",
    "RerankerComponent",
    "RetrieverComponent",
    "Retrieval",
    "apply_filters",
    "build_embedder_from_config",
    "build_reranker_from_config",
    "default_should_remember",
    "filter_by_metadata",
    "filter_by_scope",
    "filter_by_source_tool",
    "filter_by_time",
    "never_should_remember",
    "non_read_only_only",
    "on_error_only",
    "resolve_remember_filter",
    "visible_scopes_for",
    "wire_memory_components",
]
