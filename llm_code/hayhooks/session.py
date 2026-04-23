"""HayhooksSession — wraps the M3 Agent for headless consumption.

Enforces:
- ``max_agent_steps`` cap (lower than the interactive default).
- ``allowed_tools`` filter (empty tuple = allow everything).
- Simple sliding-window rate limit per session fingerprint.
- Metrics emission (step count, duration, prompt+completion tokens).

The class is transport-agnostic — both the MCP server and the
OpenAI-compatible FastAPI app construct a ``HayhooksSession`` per
request and call :meth:`run` or :meth:`run_streaming`.

The M3 ``engine/agent.py`` module lands in parallel; import is guarded
so M4 tests can mock the Agent surface until M3 ships.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

try:  # pragma: no cover — Agent lands in M3
    from llm_code.engine.agent import Agent  # type: ignore
except ImportError:  # pragma: no cover — fallback until M3 ships
    Agent = None  # type: ignore[assignment,misc]


@dataclass
class AgentResult:
    """Minimal, transport-facing shape the Agent must expose.

    The real M3 ``AgentResult`` will be richer; hayhooks only reads
    these attributes, so we declare a Protocol-compatible dataclass
    that both the real Agent and test doubles can satisfy.
    """

    text: str = ""
    exit_reason: str = "stop"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    steps: int = 0
    tool_calls: tuple[dict, ...] = ()

    def final_text(self) -> str:
        return self.text


@dataclass(frozen=True)
class SessionMetrics:
    duration_s: float
    steps: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    exit_reason: str


class RateLimitExceeded(Exception):
    """Raised by :meth:`HayhooksSession.check_rate_limit`.

    Carries an optional ``retry_after`` hint (in seconds) that the HTTP
    layer can surface as a ``Retry-After`` header so well-behaved clients
    back off until the rate-limit window re-opens.
    """

    def __init__(
        self, message: str, *, retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass
class HayhooksSession:
    """Per-request wrapper around the M3 Agent.

    ``agent`` is injected so tests can pass a mock; production code
    constructs a real Agent lazily via :meth:`_default_agent`.
    """

    config: Any = None  # HayhooksConfig — typed loosely to avoid tight coupling
    fingerprint: str = ""
    agent: Any = None
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    _metrics: list[SessionMetrics] = field(default_factory=list)
    _request_times: deque[float] = field(default_factory=deque)

    def __post_init__(self) -> None:
        if self.agent is None:
            self.agent = self._default_agent()

    # --- public API ---------------------------------------------------

    @property
    def max_steps(self) -> int:
        cfg = self.config
        return int(getattr(cfg, "max_agent_steps", 20))

    @property
    def allowed_tools(self) -> tuple[str, ...]:
        cfg = self.config
        return tuple(getattr(cfg, "allowed_tools", ()))

    @property
    def rate_limit_rpm(self) -> int:
        cfg = self.config
        return int(getattr(cfg, "rate_limit_rpm", 60))

    def check_rate_limit(self, now: float | None = None) -> None:
        """Sliding-window rate limit (per session fingerprint).

        Raises :class:`RateLimitExceeded` once ``rate_limit_rpm`` requests
        have landed in the prior 60-second window.
        """
        limit = self.rate_limit_rpm
        if limit <= 0:
            return
        ts = time.monotonic() if now is None else now
        window = self._request_times
        while window and ts - window[0] >= 60.0:
            window.popleft()
        if len(window) >= limit:
            # Time until the oldest request falls out of the 60 s window
            # — the earliest point at which a retry could succeed. We
            # ceil to an int because ``Retry-After`` is defined in whole
            # seconds (RFC 7231 §7.1.3) and most proxies reject floats.
            retry_after_s = max(1, int((window[0] + 60.0 - ts) + 0.999))
            raise RateLimitExceeded(
                f"rate limit {limit} rpm exceeded for session "
                f"{self.session_id[:8]}",
                retry_after=float(retry_after_s),
            )
        window.append(ts)

    def run(self, messages: list[dict]) -> AgentResult:
        """Synchronous path — used by non-streaming HTTP clients."""
        self.check_rate_limit()
        messages = self._filter_messages(messages)
        start = time.monotonic()
        result = self._invoke_agent_sync(messages)
        duration = time.monotonic() - start
        self._record_metrics(duration, result)
        return result

    async def run_async(self, messages: list[dict]) -> AgentResult:
        """Async path — used by MCP and OpenAI streaming endpoints."""
        self.check_rate_limit()
        messages = self._filter_messages(messages)
        start = time.monotonic()
        result = await self._invoke_agent_async(messages)
        duration = time.monotonic() - start
        self._record_metrics(duration, result)
        return result

    async def run_streaming(
        self, messages: list[dict],
    ) -> AsyncIterator[dict]:
        """Stream events from the agent as ``{type, ...}`` dicts.

        Expected event shapes (mirrors the OpenAI chunk adapter):
        - ``{"type": "text_delta", "text": "..."}``
        - ``{"type": "tool_call", "name": "...", "args": {...}}``
        - ``{"type": "tool_result", "name": "...", "output": "..."}``
        - ``{"type": "error", "message": "..."}``
        - ``{"type": "done", "result": AgentResult}``
        """
        self.check_rate_limit()
        messages = self._filter_messages(messages)
        start = time.monotonic()
        stream = getattr(self.agent, "run_streaming", None)
        if stream is None:
            # Fallback: synthesise a single text_delta from run()
            result = await self._invoke_agent_async(messages)
            yield {"type": "text_delta", "text": result.final_text()}
            yield {"type": "done", "result": result}
            self._record_metrics(time.monotonic() - start, result)
            return
        last_result: AgentResult | None = None
        async for event in stream(messages, max_steps=self.max_steps,
                                   allowed_tools=self.allowed_tools):
            if isinstance(event, dict):
                if event.get("type") == "done":
                    last_result = event.get("result", last_result)
                yield event
            else:
                yield {"type": "text_delta", "text": str(event)}
        if last_result is not None:
            self._record_metrics(time.monotonic() - start, last_result)

    # --- internals ----------------------------------------------------

    def _default_agent(self) -> Any:
        """Construct an Agent if M3 is available, else a lazy stub.

        The stub raises only if someone calls ``run()`` without first
        injecting an agent — tests always inject one.
        """
        if Agent is None:
            return _LazyAgentStub()
        try:
            return Agent()  # type: ignore[call-arg]
        except Exception:  # pragma: no cover — tolerant fallback
            return _LazyAgentStub()

    def _filter_messages(self, messages: list[dict]) -> list[dict]:
        """Apply simple passthrough; retained as a hook for future filters."""
        return list(messages)

    def _invoke_agent_sync(self, messages: list[dict]) -> AgentResult:
        run = getattr(self.agent, "run", None)
        if run is None:
            raise RuntimeError("injected agent does not implement run()")
        raw = run(
            messages,
            max_steps=self.max_steps,
            allowed_tools=self.allowed_tools,
        )
        return _coerce_result(raw)

    async def _invoke_agent_async(self, messages: list[dict]) -> AgentResult:
        run_async = getattr(self.agent, "run_async", None)
        if run_async is not None:
            raw = await run_async(
                messages,
                max_steps=self.max_steps,
                allowed_tools=self.allowed_tools,
            )
            return _coerce_result(raw)
        # Bridge a sync Agent via asyncio.to_thread
        return await asyncio.to_thread(self._invoke_agent_sync, messages)

    def _record_metrics(self, duration: float, result: AgentResult) -> None:
        pt = int(getattr(result, "prompt_tokens", 0) or 0)
        ct = int(getattr(result, "completion_tokens", 0) or 0)
        self._metrics.append(
            SessionMetrics(
                duration_s=duration,
                steps=int(getattr(result, "steps", 0) or 0),
                prompt_tokens=pt,
                completion_tokens=ct,
                total_tokens=pt + ct,
                exit_reason=str(getattr(result, "exit_reason", "stop")),
            )
        )

    @property
    def metrics(self) -> tuple[SessionMetrics, ...]:
        return tuple(self._metrics)


def _coerce_result(raw: Any) -> AgentResult:
    """Best-effort coercion from any Agent return value to AgentResult."""
    if isinstance(raw, AgentResult):
        return raw
    if raw is None:
        return AgentResult()
    if isinstance(raw, str):
        return AgentResult(text=raw)
    # Duck-typed: pull known attrs if present
    return AgentResult(
        text=str(getattr(raw, "text", "") or getattr(raw, "final_text", lambda: "")() or ""),
        exit_reason=str(getattr(raw, "exit_reason", "stop")),
        prompt_tokens=int(getattr(raw, "prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(raw, "completion_tokens", 0) or 0),
        steps=int(getattr(raw, "steps", 0) or 0),
        tool_calls=tuple(getattr(raw, "tool_calls", ()) or ()),
    )


class _LazyAgentStub:
    """Stand-in agent that fails loudly only when actually invoked."""

    def run(self, messages, **kwargs):  # noqa: ARG002 - signature compat
        raise RuntimeError(
            "llm_code.engine.agent.Agent is not available; "
            "inject a test double or complete M3 before invoking hayhooks"
        )

    async def run_async(self, messages, **kwargs):  # noqa: ARG002
        raise RuntimeError(
            "llm_code.engine.agent.Agent is not available; "
            "inject a test double or complete M3 before invoking hayhooks"
        )
