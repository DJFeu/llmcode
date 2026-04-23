# Async Migration Guide (v12 M5)

> **Status:** v12 M5 — every `@component` class gets a sync↔async
> bridge automatically. Converting a sync-only component to
> async-native is an opt-in refactor.

## Section table of contents

1. Why migrate
2. Decorator surface
3. From sync to async — step by step
4. Blocking I/O rule
5. `@assert_no_blocking_io` fixture
6. Running the async pipeline

## 1. Why migrate

Sync components run fine inside an `AsyncPipeline` — the
`ensure_run_async` bridge wraps them in `asyncio.to_thread`.
Migrate only when at least one of the following is true:

- The component spends >50ms in network I/O per run. Threading
  to a worker pool works but costs fd churn and adds scheduling
  latency.
- You need to `await` another async helper (e.g. an async
  HTTP client, `asyncio.gather` over sub-calls).
- You want fan-out within the component body (parallel LLM calls,
  concurrent tool probes).

Otherwise keep the sync surface — it's simpler, test coverage
is cheaper, and the bridge overhead is <100 µs per run.

## 2. Decorator surface

```python
from llm_code.engine.component import component, output_types
from llm_code.engine.async_component import async_component, is_async_native
```

| Decorator | Effect |
|-----------|--------|
| `@component` | Unchanged. Always outermost. |
| `@async_component` | Validates that `run_async` is `async def`; sets `__is_async_native__`; wires the sync bridge via `ensure_run`. |

`ensure_run_async(cls)` / `ensure_run(cls)` are called automatically
by `@component` — authors rarely call them directly. They are
**idempotent**: applying twice is harmless, and an inherited real
`run`/`run_async` is never overridden by a bridge.

`is_async_native(obj)` returns `True` for classes decorated with
`@async_component`. Use it in tests to skip bridge-level
assertions on authored-async components.

## 3. From sync to async — step by step

Starting point (sync-only):

```python
@component
@output_types(response=str)
class FetchPage:
    concurrency_group = "io_bound"

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def run(self, url: str) -> dict:
        r = self._client.get(url, timeout=30.0)
        return {"response": r.text}
```

### Step 1 — flip the client to async

```python
def __init__(self, client: httpx.AsyncClient) -> None:
    self._client = client
```

### Step 2 — rename `run` → `run_async` and add `async def`

```python
async def run_async(self, url: str) -> dict:
    r = await self._client.get(url, timeout=30.0)
    return {"response": r.text}
```

### Step 3 — add `@async_component`

```python
@component
@async_component
@output_types(response=str)
class FetchPage:
    concurrency_group = "io_bound"

    def __init__(self, client: "httpx.AsyncClient") -> None:
        self._client = client

    async def run_async(self, url: str) -> dict:
        r = await self._client.get(url, timeout=30.0)
        return {"response": r.text}
```

No other change is needed. The decorator synthesises a `run()`
sync bridge that:

- calls `asyncio.run(self.run_async(...))` when there's no
  running loop (e.g. CLI smoke tests), or
- raises `RuntimeError` when called from inside a running loop
  (prevents silent deadlock).

### Step 4 — update tests

```python
@pytest.mark.asyncio
@assert_no_blocking_io(threshold_s=0.05)
async def test_fetch_page_yields_response(monkeypatch):
    client = FakeAsyncClient(response_text="ok")
    comp = FetchPage(client)
    out = await comp.run_async(url="https://example.com")
    assert out == {"response": "ok"}
```

## 4. Blocking I/O rule

The blocking-I/O rule is a contract, not a lint:

> Any code path reached from an `async def run_async(...)` method
> MUST NOT perform synchronous I/O. This includes
> `open()`, `time.sleep()`, `requests.get()`, `subprocess.run`,
> and blocking stdlib calls like `socket.recv` without a timeout.

Why: `asyncio` event-loop blocking silently starves every other
Component in the same level group. A single 1-second blocking
call on a 50-iteration agent run costs an aggregate 50 seconds
of lost parallelism.

Allowed escape hatch: if you must call blocking code from an
async Component, wrap it explicitly in `asyncio.to_thread`:

```python
result = await asyncio.to_thread(cpu_bound_helper, payload)
```

This signals intent — reviewers know the thread switch is deliberate.

## 5. `@assert_no_blocking_io` fixture

```python
from llm_code.engine import assert_no_blocking_io

@pytest.mark.asyncio
@assert_no_blocking_io(threshold_s=0.1)
async def test_no_sneaky_sleeps():
    comp = MyComponent()
    await comp.run_async(payload="…")
```

Mechanism: the decorator temporarily sets
`loop.slow_callback_duration = threshold_s`, so asyncio's own
slow-callback warning fires earlier. It also promotes
`ResourceWarning` to an error for the duration of the call, so
un-awaited coroutines + unclosed sockets fail the test.

Put the decorator **after** `@pytest.mark.asyncio`. Applying it
to a helper that's never actually run by the test adds zero
cost — the decorator is inert at module import.

## 6. Running the async pipeline

```python
from llm_code.engine import AsyncPipeline

pipe = AsyncPipeline()
pipe.add_component("a", FetchPage(client))
pipe.add_component("b", ParseHTML())
pipe.connect("a.response", "b.payload")

# From an async context
out = await pipe.run_async({"a": {"url": "https://example.com"}})

# From sync (tests only; never in production)
from llm_code.engine.async_pipeline import run_via_async
out = run_via_async(pipe, {"a": {"url": "https://example.com"}})
```

The scheduler groups same-level components by `concurrency_group`
and caps each group's fan-out at
`llm_code.engine.MAX_GROUP_PARALLELISM` (default 8). Set the
attribute on your class to influence placement:

```python
class BigCPU:
    concurrency_group = "cpu_bound"    # serialises vs other cpu_bound
class LightIO:
    concurrency_group = "io_bound"     # fans out to the I/O pool
```

Groups without a name fall back to `DEFAULT_GROUP = "default"`.

The transition from sync to async is non-breaking: both pipelines
accept the same components, and the `run()` / `run_async()`
surfaces are always available on every decorated class.
