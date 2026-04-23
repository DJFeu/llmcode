# Architecture

## Layer Diagram

```
cli → runtime → tools
         ↓
       engine → api
         ↓
    mcp / lsp / marketplace / hayhooks
```

The v12 engine subsystem sits between the classic `runtime` layer
and the provider API. It is composed of six sub-systems (detailed
below): a Jinja2 Prompt Template Engine, a DAG-shaped Pipeline,
an Agent Loop, an Observability stack, an Async Execution Model,
and the Memory Components family.

## Package Map

| Package | Responsibility |
|---------|---------------|
| `api/` | LLM provider abstraction (OpenAI-compat + Anthropic) |
| `tools/` | 20 built-in tools + agent + parsing |
| `runtime/` | Conversation loop, permissions, hooks, session, memory, compression |
| `engine/` | v12 primitives — PromptBuilder, Component, Pipeline, Agent, policies, observability, memory Components |
| `hayhooks/` | Headless MCP + OpenAI-compatible transports |
| `memory/` | `MemoryLayer` backends + HIDA migration CLI |
| `mcp/` | MCP client, tool bridge, server lifecycle |
| `marketplace/` | Plugin system, 5 registries |
| `lsp/` | LSP client, auto-detector, 3 tools |
| `cli/` | REPL, streaming renderer, commands |

## Key Design Decisions

1. **Strict layer deps** — cli → runtime → engine → {tools, api}. No reverse deps.
2. **Immutable data** — Frozen dataclasses everywhere; new session on each mutation.
3. **Fail-closed safety** — Tools default to not-read-only, not-concurrent-safe.
4. **Dual-track tools** — Native function calling + XML tag fallback for any model.
5. **Progressive compression** — 4 levels, lightweight first.
6. **Observability by default** — every Component + Agent iteration emits a span; exporters are opt-in.

## Prompt Template Engine

All system prompts, mode reminders, and section fragments are
Jinja2 templates under `llm_code/engine/prompts/`.
`PromptBuilder` (`engine/prompt_builder.py`) is the only render
path; `str.format`-based assembly was removed in M1.5.

```
                     ┌──────────────┐
runtime call ───────►│ PromptBuilder│───► {"prompt": str}
                     └───────┬──────┘
                             │ StrictUndefined + required_variables
                             ▼
         llm_code/engine/prompts/
             base.j2                 (skeleton w/ {% block %}s)
             modes/<name>.j2         (plan / max_steps / build_switch)
             models/<name>.j2        (anthropic / qwen / gpt / ...)
             sections/<family>/...   (memory/, tools/, permissions/)
             reminders/<name>.j2     (injected between turns)
```

See [engine/prompt_template_author_guide.md](engine/prompt_template_author_guide.md).

## Pipeline DAG

Components register with a `Pipeline` and connect their output
sockets to downstream input sockets. The execution order is
topological; cycles are rejected at `pipeline.validate()`.

```
          ┌───────────────────┐
inputs ──►│ Pipeline          │──► outputs
          │   ┌──► PermissionCheck ──┐
          │   │                      ├──► ToolExecutor ──► Postprocess
          │   └──► RateLimiter ──────┘
          │           SocketMismatchError at connect() time
          └───────────────────┘
```

`Socket` has `(name, type, direction, required, default)`.
Type compatibility is lenient: either side being `Any`, exact
equality, or `issubclass(src, dst)` are all accepted. Typing
generics defer to run time.

See [engine/component_author_guide.md](engine/component_author_guide.md).

## Agent Loop

`Agent` drives a tool-calling loop over a Pipeline. Policies
are composable and pure — the Agent owns all State mutation.

```
 messages ──► chat_fn ──► tool_calls ─► Pipeline.run ─► tool_results
                 ▲                                              │
                 │                                              ▼
                 └── ExitCondition? ◄── RetryPolicy ◄── (error) Fallback / Degraded
                     (MaxSteps / NoProgress / DenialThreshold / ...)
```

Four `Protocol`s steer the loop: `RetryPolicy`,
`FallbackPolicy`, `DegradedModePolicy`, `ExitCondition`. Each
built-in ships with a `NoX` default so omitting a slot never
changes behaviour.

See [engine/policy_author_guide.md](engine/policy_author_guide.md).

## Observability

Every `Pipeline.run` / `Component.run` / `Agent.iteration` /
tool call emits an OpenTelemetry span. Attribute keys are
allow-listed (`ALLOWED_ATTRIBUTE_KEYS`); raw secrets and PII
pass through a regex redactor before export.

```
@component class C:        ───► component.C span
Pipeline.run()              ───► pipeline.<Class> span
Agent.iteration()           ───► agent.iteration.<N> span
tool_call_span("bash")      ───► tool.bash span (args_hash only)
api_span("claude-sonnet-4") ───► api.stream span (GenAI attrs)

 Exporters:  console ◄─── dev
             otlp    ◄─── prod (Jaeger / Tempo / Honeycomb)
             langfuse◄─── LLM SaaS (maps to generations)
```

When OpenTelemetry is not installed the whole subsystem
degrades to a no-op. See
[engine/observability_exporters.md](engine/observability_exporters.md)
and [engine/observability_redaction.md](engine/observability_redaction.md).

## Async Execution Model

Components may author either a sync `run` or an async
`run_async`. `@component` auto-wires the missing side so every
class exposes both surfaces. `AsyncPipeline` schedules
components in topological levels; within each level,
components are bucketed by `concurrency_group` and dispatched
through a bounded semaphore.

```
 level 0 ┌─ A (cpu_bound) ─┐   level 1 ┌─ D (io_bound) ─┐
        ├─ B (io_bound)   │─►        ├─ E (io_bound) ─┤─► merge
         └─ C (cpu_bound) ─┘           └─ F (cpu_bound)┘
     MAX_GROUP_PARALLELISM = 8 per group
```

See [engine/async_migration.md](engine/async_migration.md).

## Memory Components

Five Components plus a wiring helper constitute the v12 memory
subsystem. Embedder output feeds both the query path (Retriever
→ Reranker → MemoryContext → PromptAssembler) and the write
path (MemoryWriter → MemoryLayer).

```
 user prompt ─► Embedder ─► Retriever ─► Reranker ─► MemoryContext ─► PromptAssembler
                                                                    
 tool result ─► MemoryWriter ─► MemoryLayer.write()
```

Scope filter is a lattice: `SESSION ⊆ {SESSION}`,
`PROJECT ⊆ {PROJECT, GLOBAL}`, `GLOBAL ⊆ {GLOBAL}` so queries
against `PROJECT` transparently include global knowledge.

See [engine/memory_components.md](engine/memory_components.md)
and [engine/memory_migration.md](engine/memory_migration.md).
