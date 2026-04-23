# Policy Author Guide

> **Status:** v12 M3 — four `Protocol`s drive the Agent loop.
> `RetryPolicy`, `FallbackPolicy`, `DegradedModePolicy`,
> `ExitCondition`. Policies are pure, swappable objects. The Agent
> owns all `State` mutation — a policy never edits state in place.

## Section table of contents

1. The four protocols
2. `RetryPolicy` + `RetryBudget`
3. `FallbackPolicy`
4. `DegradedModePolicy`
5. `ExitCondition` + `warning_reminder` hook
6. Composing policies

## 1. The four protocols

All four live in `llm_code.engine.policies.__init__`:

```python
from llm_code.engine.policies import (
    RetryPolicy, RetryDecision,
    FallbackPolicy, FallbackDecision,
    DegradedModePolicy, DegradedDecision,
    ExitCondition,
)
```

Each protocol is `@runtime_checkable` so tests can assert
conformance with `isinstance(obj, RetryPolicy)` without a full
subclass. The decision dataclasses (`RetryDecision`, etc.) are
`frozen=True` — mutating them raises at runtime.

| Protocol | Question it answers | Return type |
|----------|---------------------|-------------|
| `RetryPolicy` | "should we re-run the failed tool?" | `RetryDecision` |
| `FallbackPolicy` | "what tool should we try instead?" | `FallbackDecision` |
| `DegradedModePolicy` | "should we restrict the tool surface?" | `DegradedDecision` |
| `ExitCondition` | "should the agent loop terminate?" | `tuple[bool, str]` |

## 2. `RetryPolicy` + `RetryBudget`

**Built-ins:**
`NoRetry` (default), `ExponentialBackoff`, `RetryOnRateLimit`,
`CompositeRetryPolicy`.

### Example: retry only on 5xx, not 4xx

```python
from llm_code.engine.policies import RetryDecision, RetryPolicy
from llm_code.engine.state import State

class RetryOn5xxOnly:
    """Retry server errors; surface client errors immediately."""

    def __init__(self, max_attempts: int = 3, base_ms: int = 500) -> None:
        self._max = max_attempts
        self._base = base_ms

    def should_retry(self, error: Exception, attempt: int, state: State) -> RetryDecision:
        status = getattr(error, "status_code", None)
        if status is None or not (500 <= status < 600):
            return RetryDecision(should_retry=False, reason=f"non-5xx ({status})")
        if attempt >= self._max:
            return RetryDecision(should_retry=False, reason="max attempts")
        return RetryDecision(
            should_retry=True,
            delay_ms=self._base * (2 ** attempt),
            reason=f"HTTP {status} — retrying",
        )
```

### `RetryBudget` interaction

`RetryBudget` sits **underneath** every `RetryPolicy` as a cross-
cutting guard. Default cap is 20 total retries per Agent run. When
the budget is exhausted, the Agent ignores a positive
`RetryDecision` and exits. Construct it explicitly if you need a
tighter ceiling:

```python
from llm_code.engine.policies.budget import RetryBudget
agent = Agent(pipeline, chat_fn=chat, retry_budget=RetryBudget(5))
```

The Agent calls `budget.can_retry()` / `budget.consume()` — authors
never need to call either. Composition with
`CompositeRetryPolicy` still honours the budget because the
Agent owns the guard.

## 3. `FallbackPolicy`

Built-ins: `NoFallback` (default), `SemanticFallback`
(static tool→tool map), `ModelFallback` (cheap-LLM suggestion,
cached per `(failed_tool, error_class_name)`).

```python
from llm_code.engine.policies.fallback import SemanticFallback

# Default mapping covers web_search → web_fetch, glob_search → bash, ...
fallback = SemanticFallback(overrides={
    "custom_search": "web_fetch",    # add
    "web_search": None,              # remove the default
})
```

`FallbackDecision(fallback_tool=None, reason="…")` means "no
suggestion; surface original error to the model". A returned
tool name that isn't in the Agent's available tool set is
ignored with a warning (see `ModelFallback._cache`) — the
policy layer must not enable hallucinated tool names.

## 4. `DegradedModePolicy`

Built-ins: `NoDegraded` (default), `ConsecutiveFailureDegraded`,
`BudgetDegraded`.

```python
from llm_code.engine.policies.degraded import (
    ConsecutiveFailureDegraded, READ_ONLY_TOOLS,
)

policy = ConsecutiveFailureDegraded(
    threshold=3,
    allowed_tools=READ_ONLY_TOOLS | frozenset({"lsp_hover"}),
)
```

Degraded mode is **sticky**: once tripped, the Agent stays
degraded for the rest of the run. Callers that want re-entry on
recovery should emit a new Agent per recovery attempt rather than
hot-swap policies mid-loop.

## 5. `ExitCondition` + `warning_reminder` hook

Built-ins: `MaxStepsReached` (always installed by default),
`NoProgress`, `ExplicitExitTool`, `DenialThreshold`,
`BudgetExhausted`, `CompositeExit`.

```python
from llm_code.engine.policies.exit import MaxStepsReached, CompositeExit, NoProgress

exit_cond = CompositeExit([
    MaxStepsReached(cap=40, warning_offset=5),
    NoProgress(window=3),
])
```

`MaxStepsReached.warning_reminder(state)` returns a short string
when `state["iteration"] == cap - warning_offset`, else `None`.
The Agent loop picks up the non-`None` return and injects it as a
`role=system` message on the next turn — the model gets a chance
to wrap up cleanly instead of being cut off. Custom exit
conditions may expose the same `warning_reminder(state)` method
to participate in that hook; missing the method is fine, it's
checked with `hasattr`.

## 6. Composing policies

The Agent factory accepts one instance per slot:

```python
from llm_code.engine.agent import Agent
from llm_code.engine.policies.retry import CompositeRetryPolicy, RetryOnRateLimit, ExponentialBackoff

agent = Agent(
    pipeline=my_pipe,
    chat_fn=my_chat_fn,
    retry_policy=CompositeRetryPolicy([RetryOnRateLimit(), ExponentialBackoff()]),
    fallback_policy=SemanticFallback(),
    degraded_policy=ConsecutiveFailureDegraded(threshold=3),
    exit_conditions=[MaxStepsReached(cap=40), NoProgress(window=3)],
)
result = agent.run(messages=[{"role": "user", "content": "…"}])
print(result.exit_reason, result.iterations)
```

`CompositeRetryPolicy` evaluates sub-policies in order — the
**first** one to return `should_retry=True` wins. Ordering
matters: put the specific policy (`RetryOnRateLimit`) before
the general fallback (`ExponentialBackoff`) so a 429 surfaces
the right delay.

Testing suggestions per policy type are documented in
`tests/test_engine/policies/` — every built-in policy has a
mirror test file. Use them as templates.
