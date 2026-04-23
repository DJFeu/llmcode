# Component Author Guide

> **Status:** v12 M2 — the `@component` decorator is the official way
> to write a Pipeline stage. Every built-in component in
> `llm_code/engine/components/` follows this recipe.

## Section table of contents

1. What a Component is
2. Decorator reference
3. Socket type compatibility
4. `Pipeline.connect` by example
5. State read / write declarations
6. Sync vs async — which surface do I author?
7. Writing a new Component from scratch
8. Testing checklist

## 1. What a Component is

A **Component** is a Python class whose `run()` (or `run_async()`)
method consumes a declared set of named inputs and produces a dict
of named outputs. The `@component` class decorator turns a plain
class into a Pipeline-registerable stage by:

1. Introspecting `run` (or `run_async`) and emitting typed
   `Socket` descriptors for each parameter.
2. Attaching the outputs declared via `@output_types(...)`.
3. Installing the sync ↔ async bridge
   (`llm_code.engine.async_component.ensure_run` /
   `ensure_run_async`) so either surface always works.
4. Wrapping the class in an OpenTelemetry span decorator
   (`traced_component`) — no call-site change needed.

## 2. Decorator reference

```python
from llm_code.engine.component import (
    component,
    output_types,
    state_reads,
    state_writes,
)
```

| Decorator | Purpose | Where the metadata lands |
|-----------|---------|--------------------------|
| `@component` | Sockets + tracing + bridge | `cls.__component_inputs__`, `__is_component__` |
| `@output_types(**types)` | Declare return-dict keys & types | `cls.__component_outputs__` |
| `@state_reads(*keys)` | Document which `State` keys are read | `cls.__state_reads__` |
| `@state_writes(*keys)` | Exclusive write declaration | `cls.__state_writes__` |

Stacking order is flexible — `@component` may be the outermost or
innermost decorator; the metadata is idempotent. The existing
components stack outermost-first in decoration order (reads
bottom-up):

```python
@traced_component                # (implicit — wired inside @component)
@component
@output_types(entries=tuple, scores=tuple)
@state_reads("query_embedding")
@state_writes("memory_entries")
class RetrieverComponent: ...
```

Two Components must **never** declare `@state_writes` on the same
key — `Pipeline.validate()` raises `ValueError` listing every
offending writer.

## 3. Socket type compatibility

`Pipeline.connect` performs a lenient static check via
`_types_compatible(src, dst)`:

| Case | Result |
|------|--------|
| `src is dst` | compatible |
| Either side is `typing.Any` | compatible |
| Both are classes and `issubclass(src, dst)` | compatible |
| `issubclass` raises `TypeError` (typing generic like `list[int]`) | compatible — defer to run time |

So you can be loose for prototypes (`Any`) and tighten as the
design solidifies. Mismatches raise
`llm_code.engine.pipeline.SocketMismatchError` at `connect()` time
— caught in unit tests, never in production.

## 4. `Pipeline.connect` by example

```python
from llm_code.engine.pipeline import Pipeline
from llm_code.engine.components.memory.embedder import EmbedderComponent
from llm_code.engine.components.memory.retriever import RetrieverComponent

pipe = Pipeline()
pipe.add_component("embedder", EmbedderComponent(backend))
pipe.add_component("retriever", RetrieverComponent(layer))

# string form: "<component>.<socket>"
pipe.connect("embedder.embedding", "retriever.embedding")

# Entry-point inputs — sockets not fed by any connection.
for name, sockets in pipe.inputs().items():
    print(name, list(sockets))

# One-shot validate (cycles + state_writes conflicts)
pipe.validate()

# Execute
out = pipe.run({"embedder": {"text": "hello world"}})
print(out["retriever"]["entries"])
```

Useful introspection helpers:

- `pipe.inputs()` — open entry sockets keyed by component name.
- `pipe.outputs()` — sockets not consumed downstream (the leaf
  outputs of the DAG).
- `pipe.to_dot()` — Graphviz DOT string for debugging.

## 5. State read / write declarations

The engine `State` is a plain `dict` passed around by the Agent.
`@state_reads` / `@state_writes` are **documentation with teeth**:

- Writes are exclusive — conflicts are a build-time error.
- Reads are informational today (used by a future static planner
  to order components without an explicit connection).
- Declare everything you actually touch. The v12 review bot
  checks that every `state["…"]` reference in `run()` has a
  matching declaration.

## 6. Sync vs async — which surface do I author?

Author exactly **one** of:

- `def run(self, **kwargs) -> dict:` — sync-native. The engine
  auto-generates a `run_async` bridge that drops into
  `asyncio.to_thread`.
- `async def run_async(self, **kwargs) -> dict:` + the extra
  `@async_component` decorator — async-native. The engine
  auto-generates a `run` sync bridge that raises when invoked
  inside a running loop (no silent deadlock).

Defining both on the same class raises `TypeError` at decoration
time unless one is a bridge marker — if you truly need a hand-
tuned pair, write `run` and let the decorator add `run_async`
itself.

See
[async_migration.md](async_migration.md) for the full migration
playbook when you convert a sync-only component to async-native.

## 7. Writing a new Component from scratch

```python
from __future__ import annotations

from typing import Any
from llm_code.engine.component import component, output_types, state_reads

@component
@output_types(normalised=str, length=int)
@state_reads("user_locale")
class LowercaseNormaliser:
    """Collapse tool names to ASCII lowercase for stable matching."""

    concurrency_group = "cpu_bound"    # optional — default is "default"

    def run(self, name: str) -> dict[str, Any]:
        if not isinstance(name, str):
            raise TypeError(f"expected str, got {type(name).__name__}")
        lowered = name.strip().lower()
        return {"normalised": lowered, "length": len(lowered)}
```

Wire it up:

```python
pipe.add_component("normaliser", LowercaseNormaliser())
pipe.connect("tool_parser.tool_name", "normaliser.name")
pipe.connect("normaliser.normalised", "permission_check.tool_name")
```

The `concurrency_group` class attribute tells
`AsyncPipeline` whether two same-level components should
share a semaphore — `"cpu_bound"` groups serialise vs `"io_bound"`
groups that fan out to the shared pool (see `concurrency.py`).

## 8. Testing checklist

For each new Component:

- [ ] `test_<name>_introspection` — assert
  `get_input_sockets(cls)["name"].required` matches the signature.
- [ ] `test_<name>_run_happy_path` — feed representative inputs,
  assert the `run()` dict has the declared output keys.
- [ ] `test_<name>_rejects_wrong_type` — a `TypeError` on bad
  input is required for every public-facing component.
- [ ] `test_<name>_socket_wiring` — `Pipeline.add_component` +
  `connect()` must succeed; assert `pipe.validate()` doesn't raise.
- [ ] If async-native: `test_<name>_run_sync_in_loop_raises` —
  calling the sync bridge from `asyncio.run` raises
  `RuntimeError` (see `ensure_run` contract).
- [ ] Coverage floor: 95% branch coverage per component file.

Tests live under `tests/test_engine/components/` mirroring the
source tree. Reuse the `MagicMock(spec=Pipeline)` fixture from
`tests/test_engine/conftest.py` when you need a pipeline stub.
