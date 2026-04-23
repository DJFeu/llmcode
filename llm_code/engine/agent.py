"""Agent — tool-calling loop over a :class:`Pipeline`.

Borrowed shape: ``haystack/components/agents/agent.py``. Our additions:
composable retry/fallback/degraded/budget policies, parity-hooked shape
so legacy :mod:`llm_code.runtime.conversation` can delegate here once
the v12 flag is flipped.

Public API:

- :class:`Agent` — construct with a Pipeline + policies; call
  :meth:`Agent.run` (sync facade) or ``await :meth:`Agent.run_async``
  to drive the loop. Both produce an :class:`AgentResult`.
- :func:`build_agent_from_config` — factory that turns the string
  policy names in :class:`~llm_code.runtime.config.AgentLoopConfig`
  into instances; meant for the runtime to use once parity is proven.

M5 async notes
--------------
``run_async`` is the canonical surface — it drives the tool loop
through the pipeline's ``run_async`` / sync bridge and can execute
independent tool calls concurrently via :func:`asyncio.gather`. The
sync ``run`` is kept as a thin facade: if invoked with no running
loop it does ``asyncio.run(self.run_async(...))``; if invoked from
*inside* a running loop it raises :class:`RuntimeError` to prevent
deadlock.

Spec: docs/superpowers/specs/2026-04-21-llm-code-v12-haystack-borrow-design.md §5.3
Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-async-pipeline.md Task 5.6
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import replace as _dc_replace
from typing import Any, Callable, Mapping, Sequence

# We do not import concrete Components — the Agent is pipeline-agnostic.
# Pipeline itself is imported purely for the static type; the tests use
# a ``MagicMock(spec=Pipeline)`` and real-world callers will pass a
# populated Pipeline built via the M2 scaffolding.
from llm_code.engine.agent_result import AgentError, AgentResult
from llm_code.engine.pipeline import Pipeline
from llm_code.engine.policies import (
    DegradedModePolicy,
    ExitCondition,
    FallbackPolicy,
    RetryPolicy,
)
from llm_code.engine.policies.budget import RetryBudget
from llm_code.engine.policies.degraded import NoDegraded
from llm_code.engine.policies.exit import CompositeExit, MaxStepsReached
from llm_code.engine.policies.fallback import NoFallback
from llm_code.engine.policies.retry import NoRetry
from llm_code.engine.state import State

logger = logging.getLogger(__name__)

# Type alias for the chat callback the Agent needs. Keeping this as a
# plain ``Callable`` (not a Protocol) means tests can pass a simple
# lambda without implementing any attributes.
ChatFn = Callable[
    [list[Any], Sequence[Any]],
    tuple[Sequence[Any], Sequence[Any]],
]


class Agent:
    """Tool-calling loop.

    The loop is intentionally synchronous. M5 adds an ``async_run``
    variant that reuses the same policy objects; until then callers
    that need an asyncio flow wrap :meth:`run` in ``asyncio.to_thread``.

    Attributes are leading-underscore by convention: policies must not
    be swapped out mid-run, so the agent exposes only :meth:`run` as
    public surface.
    """

    def __init__(
        self,
        pipeline: Pipeline,
        *,
        chat_fn: ChatFn,
        retry_policy: RetryPolicy | None = None,
        fallback_policy: FallbackPolicy | None = None,
        degraded_policy: DegradedModePolicy | None = None,
        exit_conditions: Sequence[ExitCondition] | None = None,
        retry_budget: RetryBudget | None = None,
        max_agent_steps: int = 50,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        if max_agent_steps < 1:
            raise ValueError("max_agent_steps must be >= 1")
        self._pipeline = pipeline
        self._chat = chat_fn
        self._retry = retry_policy or NoRetry()
        self._fallback = fallback_policy or NoFallback()
        self._degraded = degraded_policy or NoDegraded()
        # The MaxStepsReached default guarantees the loop always
        # terminates even when the caller forgets to pass an exit
        # condition. Extra conditions stack in priority order.
        default_exit = (MaxStepsReached(cap=max_agent_steps),)
        self._exit = CompositeExit(exit_conditions or default_exit)
        self._budget = retry_budget or RetryBudget(max_total_retries=20)
        self._max_steps = max_agent_steps
        # ``time.sleep`` is injected so tests can swap in a stub that
        # records delays instead of actually sleeping.
        self._sleep = sleep_fn or time.sleep

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, messages: Sequence[Any]) -> AgentResult:
        """Drive the tool-calling loop until an exit condition trips.

        Args:
            messages: Initial conversation — usually ``[{"role":"user", ...}]``.
                The list is copied into the State so mutations inside
                the loop don't affect the caller's list.

        Returns:
            :class:`AgentResult` with the updated message list, the
            trigger reason, and observability counters.
        """
        state: State = {
            "messages": [m for m in messages],
            "tool_calls": [],
            "tool_results": [],
            "iteration": 0,
            "denial_history": [],
            "degraded": False,
        }
        warned_once: set[int] = set()
        reminders_emitted = 0

        while True:
            # 1. Check exit conditions *before* talking to the model so
            #    a max-steps tripwire doesn't eat an extra LLM call.
            should_exit, reason = self._exit.should_exit(state)
            if should_exit:
                return self._build_result(state, reason)

            # 2. Inject any reminders from exit conditions (max_steps
            #    early warning). We gate on iteration so the reminder
            #    is emitted exactly once per cap approach.
            i = int(state.get("iteration", 0))
            for cond in self._exit.members:
                reminder_fn = getattr(cond, "warning_reminder", None)
                if reminder_fn is None:
                    continue
                if i in warned_once:
                    continue
                text = reminder_fn(state)
                if text:
                    state["messages"].append(
                        {"role": "system", "content": text}
                    )
                    warned_once.add(i)
                    reminders_emitted += 1

            # 3. Check degraded-mode trigger. Once tripped, stays tripped.
            if not state.get("degraded"):
                decision = self._degraded.check(state)
                if decision.should_degrade:
                    state["degraded"] = True
                    if decision.allowed_tools:
                        state["allowed_tools"] = decision.allowed_tools
                    if decision.reason:
                        state["messages"].append(
                            {
                                "role": "system",
                                "content": (
                                    f"Entering read-only mode: {decision.reason}."
                                ),
                            }
                        )

            # 4. Ask the model what to do next. Tools are filtered by
            #    the degraded-mode allowlist when set.
            tools = self._filtered_tools(state)
            tool_calls, text_chunks = self._chat(state["messages"], tools)

            # Record assistant text before tool calls so the message
            # order matches what a streaming renderer would show.
            self._append_assistant_text(state, text_chunks)

            if not tool_calls:
                # Model produced a final response — we're done.
                return self._build_result(state, "model_responded")

            # 5. Execute each tool call through the retry/fallback stack.
            for tc in tool_calls:
                state["tool_calls"].append(tc)
                result = self._execute_with_policies(tc, state)
                state["tool_results"].append(result)
                state["messages"].append(
                    {
                        "role": "tool",
                        "tool_call_id": _get_attr(tc, "id", ""),
                        "tool_name": _get_attr(tc, "tool_name", ""),
                        "content": getattr(result, "content", str(result)),
                        "is_error": getattr(result, "is_error", False),
                    }
                )
                if getattr(result, "is_error", False) and _is_denied(result):
                    state["denial_history"].append(tc)

            state["iteration"] = i + 1

    # ------------------------------------------------------------------
    # M5 — Async surface
    # ------------------------------------------------------------------

    async def run_async(self, messages: Sequence[Any]) -> AgentResult:
        """Async-native tool-calling loop.

        Semantically identical to :meth:`run` but:

        - Awaits the pipeline via its ``run_async`` surface when the
          pipeline supports it; falls back to ``pipeline.run`` on a
          thread for legacy pipelines.
        - Awaits the chat callback when it is ``async def``; bridges
          sync callbacks via :func:`asyncio.to_thread`.
        - Retry ``sleep`` becomes :func:`asyncio.sleep`.
        - Independent tool calls within a single assistant turn dispatch
          concurrently via :func:`asyncio.gather` — preserving order of
          results when appending to ``state["messages"]``.
        """
        state: State = {
            "messages": [m for m in messages],
            "tool_calls": [],
            "tool_results": [],
            "iteration": 0,
            "denial_history": [],
            "degraded": False,
        }
        warned_once: set[int] = set()

        while True:
            should_exit, reason = self._exit.should_exit(state)
            if should_exit:
                return self._build_result(state, reason)

            i = int(state.get("iteration", 0))
            for cond in self._exit.members:
                reminder_fn = getattr(cond, "warning_reminder", None)
                if reminder_fn is None:
                    continue
                if i in warned_once:
                    continue
                text = reminder_fn(state)
                if text:
                    state["messages"].append(
                        {"role": "system", "content": text}
                    )
                    warned_once.add(i)

            if not state.get("degraded"):
                decision = self._degraded.check(state)
                if decision.should_degrade:
                    state["degraded"] = True
                    if decision.allowed_tools:
                        state["allowed_tools"] = decision.allowed_tools
                    if decision.reason:
                        state["messages"].append(
                            {
                                "role": "system",
                                "content": (
                                    f"Entering read-only mode: {decision.reason}."
                                ),
                            }
                        )

            tools = self._filtered_tools(state)
            tool_calls, text_chunks = await self._chat_async(
                state["messages"], tools
            )

            self._append_assistant_text(state, text_chunks)

            if not tool_calls:
                return self._build_result(state, "model_responded")

            # Record tool_calls in order first so denial history / tests
            # see deterministic ordering irrespective of gather() interleaving.
            for tc in tool_calls:
                state["tool_calls"].append(tc)
            # Fan out: concurrent dispatch through the retry stack.
            results = await asyncio.gather(
                *(self._execute_with_policies_async(tc, state) for tc in tool_calls)
            )
            for tc, result in zip(tool_calls, results):
                state["tool_results"].append(result)
                state["messages"].append(
                    {
                        "role": "tool",
                        "tool_call_id": _get_attr(tc, "id", ""),
                        "tool_name": _get_attr(tc, "tool_name", ""),
                        "content": getattr(result, "content", str(result)),
                        "is_error": getattr(result, "is_error", False),
                    }
                )
                if getattr(result, "is_error", False) and _is_denied(result):
                    state["denial_history"].append(tc)

            state["iteration"] = i + 1

    async def _chat_async(
        self, messages: list[Any], tools: Sequence[Any]
    ) -> tuple[Sequence[Any], Sequence[Any]]:
        """Invoke ``self._chat`` — awaits if async, else bridges to thread.

        The :data:`ChatFn` type alias is kept lenient for backward-compat
        with synchronous test doubles; we duck-type at call time.
        """
        if inspect.iscoroutinefunction(self._chat):
            return await self._chat(messages, tools)  # type: ignore[misc]
        # Sync callback — call it on a thread so long-running impls
        # don't block the event loop. Tests commonly pass a tiny
        # lambda; the to_thread overhead is negligible vs. real LLM RTT.
        return await asyncio.to_thread(self._chat, messages, tools)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _execute_with_policies_async(
        self, tool_call: Any, state: State
    ) -> Any:
        """Async twin of :meth:`_execute_with_policies`.

        Awaits the pipeline via ``run_async`` when the pipeline supports
        it; falls back to running the sync ``run`` on a thread. Retry
        sleep is :func:`asyncio.sleep`, so the loop never pauses the
        event loop during backoff.
        """
        attempt = 0
        current = tool_call
        while True:
            try:
                run_async = getattr(self._pipeline, "run_async", None)
                if run_async is not None and inspect.iscoroutinefunction(run_async):
                    pipeline_result = await run_async({"exec": {"tool_call": current}})
                else:
                    pipeline_result = await asyncio.to_thread(
                        self._pipeline.run, {"exec": {"tool_call": current}}
                    )
                formatted = _extract_tool_result(pipeline_result, current)
                return formatted
            except Exception as exc:  # noqa: BLE001 - policy fan-out
                # --- Retry stage ---
                if self._budget.can_retry():
                    decision = self._retry.should_retry(exc, attempt, state)
                    if decision.should_retry:
                        self._budget.consume()
                        attempt += 1
                        if decision.delay_ms:
                            await asyncio.sleep(decision.delay_ms / 1000.0)
                        if decision.modified_args is not None:
                            current = _with_args(current, decision.modified_args)
                        logger.debug(
                            "retrying tool=%s attempt=%d reason=%s (async)",
                            _get_attr(current, "tool_name", "?"),
                            attempt,
                            decision.reason,
                        )
                        continue
                # --- Fallback stage ---
                if self._budget.can_retry():
                    fb = self._fallback.fallback(
                        _get_attr(current, "tool_name", ""),
                        exc,
                        state,
                    )
                    if fb.fallback_tool is not None:
                        self._budget.consume()
                        current = _with_tool(current, fb.fallback_tool)
                        attempt = 0
                        logger.debug(
                            "falling back tool=%s reason=%s (async)",
                            fb.fallback_tool,
                            fb.reason,
                        )
                        continue
                # --- Surface the error ---
                return AgentError(
                    content=(
                        f"Tool {_get_attr(current, 'tool_name', '?')!r} "
                        f"failed: {exc}"
                    ),
                    tool_call_id=_get_attr(current, "id", ""),
                    tool_name=_get_attr(current, "tool_name", ""),
                    is_error=True,
                )

    def _execute_with_policies(
        self, tool_call: Any, state: State
    ) -> Any:
        """Dispatch one tool call through the retry/fallback chain.

        Returns an :class:`AgentError` on exhaustion, or whatever the
        pipeline produced on success. The policies are the only place
        where the budget is decremented so a bug in this method can't
        silently overspend.
        """
        attempt = 0
        current = tool_call
        while True:
            try:
                pipeline_result = self._pipeline.run(
                    {"exec": {"tool_call": current}}
                )
                formatted = _extract_tool_result(pipeline_result, current)
                return formatted
            except Exception as exc:  # noqa: BLE001 - policy fan-out
                # --- Retry stage ---
                if self._budget.can_retry():
                    decision = self._retry.should_retry(exc, attempt, state)
                    if decision.should_retry:
                        self._budget.consume()
                        attempt += 1
                        if decision.delay_ms:
                            self._sleep(decision.delay_ms / 1000.0)
                        if decision.modified_args is not None:
                            current = _with_args(current, decision.modified_args)
                        logger.debug(
                            "retrying tool=%s attempt=%d reason=%s",
                            _get_attr(current, "tool_name", "?"),
                            attempt,
                            decision.reason,
                        )
                        continue
                # --- Fallback stage ---
                if self._budget.can_retry():
                    fb = self._fallback.fallback(
                        _get_attr(current, "tool_name", ""),
                        exc,
                        state,
                    )
                    if fb.fallback_tool is not None:
                        self._budget.consume()
                        current = _with_tool(current, fb.fallback_tool)
                        attempt = 0
                        logger.debug(
                            "falling back tool=%s reason=%s",
                            fb.fallback_tool,
                            fb.reason,
                        )
                        continue
                # --- Surface the error ---
                return AgentError(
                    content=(
                        f"Tool {_get_attr(current, 'tool_name', '?')!r} "
                        f"failed: {exc}"
                    ),
                    tool_call_id=_get_attr(current, "id", ""),
                    tool_name=_get_attr(current, "tool_name", ""),
                    is_error=True,
                )

    def _filtered_tools(self, state: State) -> Sequence[Any]:
        """Filter pipeline tools by the degraded-mode allowlist.

        The Pipeline may or may not expose a ``tools`` attribute in M2.
        We duck-type: when it's missing we fall back to an empty tuple,
        which means "the chat_fn sees no native tools" — appropriate
        for the synthetic tests in this milestone.
        """
        tools = getattr(self._pipeline, "tools", ())
        allowed = state.get("allowed_tools")
        if not allowed:
            return tools
        filtered: list[Any] = []
        for t in tools:
            name = getattr(t, "name", None) or (
                t.get("name") if isinstance(t, dict) else None
            )
            if name in allowed:
                filtered.append(t)
        return filtered

    def _append_assistant_text(
        self, state: State, chunks: Sequence[Any]
    ) -> None:
        """Aggregate streaming text chunks into a single assistant message."""
        text = ""
        for chunk in chunks or ():
            if isinstance(chunk, str):
                text += chunk
            elif isinstance(chunk, dict) and "text" in chunk:
                text += str(chunk["text"])
            else:
                text += getattr(chunk, "text", "")
        if text:
            state["messages"].append(
                {"role": "assistant", "content": text}
            )

    def _build_result(self, state: State, reason: str) -> AgentResult:
        """Freeze the live :class:`State` into an :class:`AgentResult`."""
        final_text = ""
        for msg in reversed(state["messages"]):
            if isinstance(msg, Mapping) and msg.get("role") == "assistant":
                final_text = str(msg.get("content", ""))
                break
        return AgentResult(
            messages=list(state["messages"]),
            exit_reason=reason,
            iterations=int(state.get("iteration", 0)),
            degraded=bool(state.get("degraded", False)),
            retries_used=self._budget.used,
            tool_results=tuple(state.get("tool_results", [])),
            final_text=final_text,
        )


# ---------------------------------------------------------------------------
# Module-level helpers (used by the Agent and by the config factory)
# ---------------------------------------------------------------------------


def _get_attr(obj: Any, name: str, default: Any = None) -> Any:
    """Duck-typing attribute lookup for ``(attr | dict-key)`` tool calls."""
    if hasattr(obj, name):
        return getattr(obj, name)
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return default


def _with_args(call: Any, args: Mapping[str, Any]) -> Any:
    """Return a copy of ``call`` with ``args`` replaced.

    Supports three concrete shapes:

    - Object with ``with_args(args)`` method — canonical v12 shape.
    - ``dataclass`` instance — :func:`dataclasses.replace` on the
      ``args`` field.
    - Mapping — a shallow copy with the ``"args"`` key overwritten.
    """
    if hasattr(call, "with_args"):
        return call.with_args(dict(args))
    try:
        return _dc_replace(call, args=dict(args))
    except TypeError:
        pass
    if isinstance(call, Mapping):
        new = dict(call)
        new["args"] = dict(args)
        return new
    raise TypeError(f"cannot replace args on {type(call).__name__}")


def _with_tool(call: Any, tool_name: str) -> Any:
    """Return a copy of ``call`` with the tool name swapped."""
    if hasattr(call, "with_tool"):
        return call.with_tool(tool_name)
    try:
        return _dc_replace(call, tool_name=tool_name)
    except TypeError:
        pass
    if isinstance(call, Mapping):
        new = dict(call)
        new["tool_name"] = tool_name
        return new
    raise TypeError(f"cannot swap tool on {type(call).__name__}")


def _extract_tool_result(pipeline_output: Any, tool_call: Any) -> Any:
    """Pull the formatted tool result out of a pipeline output dict.

    The v12 pipeline convention is that the post-processing stage
    returns a dict with ``{"formatted_result": ...}`` under its
    component name. We walk the output dict to find it; in the
    current milestone we tolerate any shape because the pipeline
    surface isn't yet frozen.
    """
    if isinstance(pipeline_output, Mapping):
        if "post" in pipeline_output:
            post = pipeline_output["post"]
            if isinstance(post, Mapping) and "formatted_result" in post:
                return post["formatted_result"]
        # Second-best: any nested ``formatted_result`` key.
        for v in pipeline_output.values():
            if isinstance(v, Mapping) and "formatted_result" in v:
                return v["formatted_result"]
    return pipeline_output


def _is_denied(result: Any) -> bool:
    """Loose detection of 'permission denied' error shapes."""
    content = str(getattr(result, "content", "")).lower()
    return "permission" in content and "denied" in content


# ---------------------------------------------------------------------------
# Config factory (Task 3.8 Step 2)
# ---------------------------------------------------------------------------


def build_agent_from_config(
    config: Any,
    pipeline: Pipeline,
    chat_fn: ChatFn,
    *,
    fallback_tools: tuple[str, ...] = (),
    suggest_fn: Callable[..., Any] | None = None,
    usage_fn: Callable[[State], float] | None = None,
) -> Agent:
    """Build an :class:`Agent` from an :class:`AgentLoopConfig`.

    The factory keeps all string-to-instance resolution in one place
    so the config stays dumb (plain strings) and the runtime doesn't
    need to know about every policy class.

    Unknown policy names raise :class:`ValueError` — we'd rather fail
    at load time than silently fall back to a less-safe default.
    """
    from llm_code.engine.policies.degraded import (  # local to avoid cycles
        BudgetDegraded,
        ConsecutiveFailureDegraded,
        NoDegraded,
    )
    from llm_code.engine.policies.exit import (
        BudgetExhausted,
        DenialThreshold,
        ExplicitExitTool,
        MaxStepsReached,
        NoProgress,
    )
    from llm_code.engine.policies.fallback import (
        ModelFallback,
        NoFallback,
        SemanticFallback,
    )
    from llm_code.engine.policies.retry import (
        CompositeRetryPolicy,
        ExponentialBackoff,
        NoRetry,
        RetryOnRateLimit,
    )

    # --- Retry ---
    rp_name = str(getattr(config, "retry_policy", "no_retry"))
    rp_max = int(getattr(config, "retry_max_attempts", 3))
    retry: RetryPolicy
    if rp_name == "no_retry":
        retry = NoRetry()
    elif rp_name == "exponential":
        retry = ExponentialBackoff(max_attempts=rp_max)
    elif rp_name == "rate_limit":
        retry = RetryOnRateLimit(max_attempts=rp_max)
    elif rp_name == "composite":
        retry = CompositeRetryPolicy(
            [RetryOnRateLimit(max_attempts=rp_max), ExponentialBackoff(max_attempts=rp_max)]
        )
    else:
        raise ValueError(f"unknown retry_policy: {rp_name!r}")

    # --- Fallback ---
    fb_name = str(getattr(config, "fallback_policy", "none"))
    fallback: FallbackPolicy
    if fb_name == "none":
        fallback = NoFallback()
    elif fb_name == "semantic":
        fallback = SemanticFallback()
    elif fb_name == "model":
        if suggest_fn is None or not fallback_tools:
            raise ValueError(
                "fallback_policy='model' requires suggest_fn and fallback_tools"
            )
        fallback = ModelFallback(suggest_fn, fallback_tools)
    else:
        raise ValueError(f"unknown fallback_policy: {fb_name!r}")

    # --- Degraded ---
    dg_name = str(getattr(config, "degraded_policy", "none"))
    dg_threshold = int(getattr(config, "degraded_threshold", 3))
    degraded: DegradedModePolicy
    if dg_name == "none":
        degraded = NoDegraded()
    elif dg_name == "consecutive_failure":
        degraded = ConsecutiveFailureDegraded(threshold=dg_threshold)
    elif dg_name == "budget":
        if usage_fn is None:
            raise ValueError("degraded_policy='budget' requires usage_fn")
        degraded = BudgetDegraded(usage_fn=usage_fn)
    else:
        raise ValueError(f"unknown degraded_policy: {dg_name!r}")

    # --- Exit conditions ---
    max_steps = int(getattr(config, "max_agent_steps", 50))
    names: tuple[str, ...] = tuple(getattr(config, "exit_conditions", ("max_steps",)))
    exit_list: list[ExitCondition] = []
    for n in names:
        if n == "max_steps":
            exit_list.append(MaxStepsReached(cap=max_steps))
        elif n == "no_progress":
            exit_list.append(NoProgress())
        elif n == "explicit_exit":
            exit_list.append(ExplicitExitTool())
        elif n == "denial_threshold":
            exit_list.append(DenialThreshold())
        elif n == "budget_exhausted":
            if usage_fn is None:
                raise ValueError(
                    "exit_conditions contains 'budget_exhausted' but usage_fn is None"
                )
            exit_list.append(BudgetExhausted(usage_fn=usage_fn))
        else:
            raise ValueError(f"unknown exit condition: {n!r}")
    if not exit_list:
        exit_list = [MaxStepsReached(cap=max_steps)]

    budget = RetryBudget(max_total_retries=int(getattr(config, "retry_budget", 20)))

    return Agent(
        pipeline,
        chat_fn=chat_fn,
        retry_policy=retry,
        fallback_policy=fallback,
        degraded_policy=degraded,
        exit_conditions=exit_list,
        retry_budget=budget,
        max_agent_steps=max_steps,
    )


__all__ = ["Agent", "ChatFn", "build_agent_from_config"]
