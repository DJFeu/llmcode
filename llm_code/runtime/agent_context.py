"""Agent context isolation via Python contextvars.

Equivalent to claude-code's ``AsyncLocalStorage<AgentContext>``
(``utils/agentContext.ts``).

Problem: when multiple agents run concurrently in the same process
(e.g. background fork children via asyncio.gather), they share
global state.  Without isolation, Agent A's telemetry events could
wrongly attribute to Agent B.

Solution: each async execution chain gets its own ``AgentContext``
via ``contextvars.ContextVar``.  The context propagates through
``await`` chains automatically.

Risk mitigations:
    - ``get_agent_context()`` returns ``None`` for the root session
      (no context set) — callers must handle gracefully.
    - ``run_with_agent_context()`` uses a context manager pattern
      that always resets the token (exception-safe).
    - All fields are immutable after creation except
      ``_invocation_emitted`` (mutable flag for one-shot telemetry).
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, TypeVar

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Agent context types
# ---------------------------------------------------------------------------

@dataclass
class AgentContext:
    """Identity and metadata for a running agent.

    Fields mirror claude-code's ``SubagentContext`` and
    ``TeammateAgentContext`` (discriminated union via ``agent_type``).
    """
    agent_id: str
    agent_type: Literal["subagent", "teammate"] = "subagent"
    name: str = ""
    is_builtin: bool = True
    invocation_kind: Literal["spawn", "resume"] | None = None
    # Mutable: consumed exactly once per spawn/resume for telemetry
    _invocation_emitted: bool = field(default=False, repr=False)

    def consume_invocation(self) -> Literal["spawn", "resume"] | None:
        """Return the invocation kind exactly once, then None.

        Prevents double-emission of telemetry edges for the same
        spawn/resume boundary.
        """
        if self._invocation_emitted:
            return None
        self._invocation_emitted = True
        return self.invocation_kind


# ---------------------------------------------------------------------------
# Context variable
# ---------------------------------------------------------------------------

_current_agent: contextvars.ContextVar[AgentContext | None] = (
    contextvars.ContextVar("current_agent", default=None)
)


def get_agent_context() -> AgentContext | None:
    """Return the current agent context, or None for the root session."""
    return _current_agent.get()


def get_agent_id() -> str | None:
    """Convenience: return the current agent_id or None."""
    ctx = _current_agent.get()
    return ctx.agent_id if ctx is not None else None


def run_with_agent_context(ctx: AgentContext, fn: Callable[..., T], *args: Any) -> T:
    """Execute *fn* within an isolated agent context.

    The context is automatically reset when *fn* returns or raises,
    so parent or sibling contexts are never affected.

    Works for both sync and async callables: for async, the
    contextvars propagate through ``await`` chains automatically.
    """
    token = _current_agent.set(ctx)
    try:
        return fn(*args)
    finally:
        _current_agent.reset(token)


async def arun_with_agent_context(ctx: AgentContext, coro: Any) -> Any:
    """Async variant of ``run_with_agent_context``.

    Sets the context before awaiting *coro* and resets afterwards.
    """
    token = _current_agent.set(ctx)
    try:
        return await coro
    finally:
        _current_agent.reset(token)
