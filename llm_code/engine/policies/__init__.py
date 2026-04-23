"""Agent loop policies — Protocols and shared decision types.

Borrowed shape: ``haystack/components/agents/agent.py`` (max_agent_steps,
exit_conditions) + ``langchain/schema/exception.py`` (retry decisions).
Not borrowed: concrete implementations from either library.

Public surface:

- :class:`RetryDecision`, :class:`FallbackDecision`, :class:`DegradedDecision`
  — frozen dataclasses returned from policy hooks; they make the policy
  output inspectable (and mockable) without having to stub out the
  whole :class:`Agent`.
- :class:`RetryPolicy`, :class:`FallbackPolicy`, :class:`DegradedModePolicy`,
  :class:`ExitCondition` — :class:`typing.Protocol` shapes used by
  :class:`~llm_code.engine.agent.Agent`. They are ``runtime_checkable``
  so we can assert protocol conformance in tests without a full subclass.

Spec: docs/superpowers/specs/2026-04-21-llm-code-v12-haystack-borrow-design.md §5.3
Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-agent-loop-control.md Task 3.1
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable

# ``State`` lives in a sibling module with no runtime cost; importing it
# lets policy authors type the ``state`` parameter directly. We keep the
# import inside this package boundary so adding new protocols only
# requires touching this single file.
from llm_code.engine.state import State


@dataclass(frozen=True)
class RetryDecision:
    """Outcome of a :class:`RetryPolicy` consultation.

    Attributes:
        should_retry: ``True`` if the Agent should re-run the failed
            tool call.
        delay_ms: Pause before the retry, in milliseconds. ``0`` means
            "retry immediately".
        modified_args: If non-``None``, the Agent should substitute this
            arg dict on the next attempt (used by e.g. rate-limit policy
            to strip a bad parameter, or by adaptive policies to narrow
            a query).
        reason: Human-readable explanation; surfaced in logs/traces. A
            non-empty reason is strongly recommended so operators can
            diagnose pathological retry chains.
    """

    should_retry: bool
    delay_ms: int = 0
    modified_args: Mapping[str, Any] | None = None
    reason: str = ""


@dataclass(frozen=True)
class FallbackDecision:
    """Outcome of a :class:`FallbackPolicy` consultation.

    Attributes:
        fallback_tool: Name of the replacement tool. ``None`` means the
            policy has no opinion and the Agent should surface the
            original error to the model.
        reason: Human-readable explanation for observability.
    """

    fallback_tool: str | None
    reason: str = ""


@dataclass(frozen=True)
class DegradedDecision:
    """Outcome of a :class:`DegradedModePolicy` consultation.

    Attributes:
        should_degrade: ``True`` if the Agent should drop to the
            declared capability subset.
        allowed_tools: Tool-name allowlist to enforce while degraded.
            Empty means "no restriction" (the default for policies that
            only observe, never restrict).
        reason: Human-readable explanation; emitted to the model as a
            system message so it knows why its tool surface shrank.
    """

    should_degrade: bool
    allowed_tools: frozenset[str] = frozenset()
    reason: str = ""


# ---------------------------------------------------------------------------
# Protocol definitions
# ---------------------------------------------------------------------------


@runtime_checkable
class RetryPolicy(Protocol):
    """Decide whether a failed tool call should be retried.

    ``attempt`` is 0-based: the first retry attempt is 0, the second is
    1, and so on. ``state`` is the live engine :class:`State` dict so
    policies can inspect context (e.g. ``iteration``, ``denial_history``).
    Implementations must be pure with respect to ``state``; the Agent
    mutates the State, not the policy.
    """

    def should_retry(
        self, error: Exception, attempt: int, state: State
    ) -> RetryDecision: ...


@runtime_checkable
class FallbackPolicy(Protocol):
    """Pick a replacement tool when a call fails and retry is exhausted."""

    def fallback(
        self, failed_tool: str, error: Exception, state: State
    ) -> FallbackDecision: ...


@runtime_checkable
class DegradedModePolicy(Protocol):
    """Inspect the live State after each iteration; trip a capability cut.

    Degraded mode is sticky: once tripped, the Agent stays degraded for
    the rest of the run. Policies that re-evaluate every iteration are
    responsible for returning a stable decision.
    """

    def check(self, state: State) -> DegradedDecision: ...


@runtime_checkable
class ExitCondition(Protocol):
    """Evaluate whether the Agent loop should terminate.

    Return value is ``(should_exit, reason)``. The reason is required
    when ``should_exit`` is ``True`` and surfaces in :class:`AgentResult`.
    """

    def should_exit(self, state: State) -> tuple[bool, str]: ...


__all__ = [
    "DegradedDecision",
    "DegradedModePolicy",
    "ExitCondition",
    "FallbackDecision",
    "FallbackPolicy",
    "RetryDecision",
    "RetryPolicy",
]
