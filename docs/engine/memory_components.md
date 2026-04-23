# Memory Components (v12 M7)

> **Status:** v12 M7 — five Pipeline Components compose the memory
> subsystem: `EmbedderComponent`, `RetrieverComponent`, Reranker
> family, `MemoryWriterComponent`, `MemoryContextComponent`. Wire
> them with `wire_memory_components(pipeline, config, layer)` or
> compose by hand for custom topologies.

## Section table of contents

1. Pipeline topology
2. `EmbedderComponent`
3. `RetrieverComponent`
4. Reranker family
5. `MemoryWriterComponent`
6. `MemoryContextComponent`
7. End-to-end wiring example
8. Scope filter semantics

## 1. Pipeline topology

```
                                                          ┌──────────────────┐
  user prompt ──► Embedder ──► Retriever ──► Reranker ──► │ MemoryContext    │──► PromptAssembler
                                                          └──────────────────┘

  tool result ──► (Embedder inside) ──► MemoryWriter ──► MemoryLayer (persist)
```

The same `EmbedderComponent` instance is reused on both paths so
the model is loaded exactly once. The writer Component owns the
inline re-embed of the summarised tool output before persistence.

## 2. `EmbedderComponent`

```python
from llm_code.engine.components.memory.embedder import (
    EmbedderComponent, build_embedder_from_config, DeterministicHashBackend,
)

embedder = build_embedder_from_config(memory_config)
# or hand-roll:
embedder = EmbedderComponent(backend=DeterministicHashBackend(dimension=384))
```

| Aspect | Value |
|--------|-------|
| Inputs | `text: str` |
| Outputs | `embedding: tuple[float, ...]`, `dimension: int` |
| `@state_writes` | `query_embedding` |
| `concurrency_group` | `"io_bound"` |
| Backends | `sentence_transformers` (default), `openai`, `anthropic`, `onnx`, `deterministic` |

Backend selection falls back to `DeterministicHashBackend` with a
logged warning when the preferred optional dep isn't importable
— the pipeline never crashes at build time because of a missing
embedding library.

## 3. `RetrieverComponent`

```python
from llm_code.engine.components.memory.retriever import RetrieverComponent
from llm_code.engine.components.memory.schema import MemoryScope

retriever = RetrieverComponent(
    layer=memory_layer,
    default_top_k=20,
    default_scope=MemoryScope.PROJECT,
)
```

| Aspect | Value |
|--------|-------|
| Inputs | `embedding`, `query` (str), `scope`, `scope_filters`, `top_k` |
| Outputs | `entries: tuple[MemoryEntry, ...]`, `scores: tuple[float, ...]`, `retrieval: Retrieval` |
| `@state_reads` | `query_embedding` |
| `@state_writes` | `memory_entries` |
| `concurrency_group` | `"io_bound"` |

The component queries every scope visible from the requested
scope (see §8), merges by score, and caps at `top_k`. The
`Retrieval` struct on the `retrieval` output packages the query,
the embedding, and a UTC `retrieved_at` timestamp for downstream
consumers that need context beyond the parallel arrays.

## 4. Reranker family

```python
from llm_code.engine.components.memory.reranker import (
    NoopReranker, CrossEncoderReranker, LLMReranker, build_reranker_from_config,
)

reranker = build_reranker_from_config(memory_config, llm_provider=my_cheap_client)
```

| Implementation | Deps | Behaviour |
|---------------|------|-----------|
| `NoopReranker` (default) | none | Pass-through; preserves `retriever` ordering. |
| `CrossEncoderReranker` | `onnxruntime` + `transformers` (via `llmcode[memory-rerank]`) | Lazy-loaded cross-encoder scorer; falls back to Noop silently when the extras are missing. |
| `LLMReranker` | caller-provided scorer callable | 1-hour TTL cache keyed by `(query_hash, entry_id)`; failing provider calls degrade to score 0.0 without crashing. |

Every reranker presents the same socket signature:

| Inputs | Outputs |
|--------|---------|
| `candidates`, `scores`, `query`, `top_k` | `entries`, `scores` |

Swapping implementations is a one-line config change.

## 5. `MemoryWriterComponent`

```python
from llm_code.engine.components.memory.writer import (
    MemoryWriterComponent, resolve_remember_filter, on_error_only,
)

writer = MemoryWriterComponent(
    layer=memory_layer,
    embedder=embedder,                        # reuse the query-side instance
    remember_filter="non_read_only_only",     # or a custom callable
    max_chars=4000,
    default_scope=MemoryScope.PROJECT,
)
```

Predicate names (strings) resolve via `_POLICY_REGISTRY`:

- `"always"` — persist every call (default)
- `"never"` — testing-only; persist nothing
- `"on_error_only"` — keep the post-mortem tool outcomes
- `"non_read_only_only"` — skip `read_file`, `glob_search`,
  `grep_search`, `ls`, git read commands

Pass a `Callable[(str, Any, bool), bool]` for full control.

| Aspect | Value |
|--------|-------|
| Inputs | `tool_call: dict`, `tool_result: Any`, `is_error: bool`, `scope` |
| Outputs | `entry_id: str`, `written: bool` |
| `@state_writes` | `memory_writes` |

The component writes a fully populated `MemoryEntry` with a UUID
id, the summarised text, the embedding, scope, `source_tool`, and
`metadata={"is_error": ...}`.

## 6. `MemoryContextComponent`

```python
from llm_code.engine.components.memory.context import MemoryContextComponent

context = MemoryContextComponent(
    template="default",     # or "compact" — swap without touching the graph
    max_chars=4000,
)
```

Templates live under
`llm_code/engine/prompts/sections/memory/<name>.j2` and are loaded
through `PromptBuilder`. Drop a new template in that directory to
add a variant — no code change required. See
[prompt_template_author_guide.md](prompt_template_author_guide.md).

| Aspect | Value |
|--------|-------|
| Inputs | `entries: tuple[MemoryEntry, ...]` |
| Outputs | `memory_context: str`, `entry_count: int` |
| `@state_reads` | `memory_entries` |
| `concurrency_group` | `"cpu_bound"` |

Empty input yields empty output so the downstream
`PromptAssembler` can drop the section entirely without a special
case.

## 7. End-to-end wiring example

The helper `wire_memory_components` does the full wiring so
callers don't copy-paste the six `Pipeline.connect` calls:

```python
from llm_code.engine.pipeline import Pipeline
from llm_code.engine.components.memory.wire import wire_memory_components
from llm_code.memory.layer import InMemoryMemoryLayer
from llm_code.runtime.config import load_config

cfg = load_config()
layer = InMemoryMemoryLayer()
pipeline = Pipeline()

wiring = wire_memory_components(pipeline, cfg.engine.memory, layer)
if wiring.enabled:
    print("memory components added:", pipeline._components.keys())
```

`wire_memory_components` is a no-op (all handles `None`, `enabled
= False`) when `memory.enabled = False`, preserving the
"memory-disabled ≡ pre-M7 byte-identical" parity test.

## 8. Scope filter semantics

Scope visibility is declared in
`llm_code/engine/components/memory/filters.py::visible_scopes_for`:

| Requested | Visible |
|-----------|---------|
| `SESSION` | `{SESSION}` |
| `PROJECT` | `{PROJECT, GLOBAL}` |
| `GLOBAL` | `{GLOBAL}` |

The `Retriever` fetches from every visible scope and merges —
always use `PROJECT` as the default so both project-scoped and
global entries are discoverable, with session scope reserved for
ephemeral or PII-sensitive content.

`scope_filters` is the extension point for non-scope criteria:

```python
{
    "source_tool": "bash",
    "created_after": "2026-04-01T00:00:00Z",
    "created_before": "2026-05-01T00:00:00Z",
    "metadata.is_error": True,
}
```

Unknown keys are ignored (the backend is free to push them down
to its native query language). Invalid ISO timestamps are treated
as "no bound" so a stale config never crashes the pipeline.
