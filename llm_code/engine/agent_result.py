"""Result / error dataclasses returned by :class:`~llm_code.engine.agent.Agent`.

Both types are frozen dataclasses: the Agent builds them at the end
of a run (or at tool-call boundaries for errors) and the caller must
not mutate them. This keeps logging / telemetry stable and makes
diffing parity fixtures a straight equality check.

Spec: docs/superpowers/specs/2026-04-21-llm-code-v12-haystack-borrow-design.md §5.3
Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-agent-loop-control.md Task 3.7
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AgentError:
    """Surface an in-loop tool failure to the model as a tool result.

    The Agent converts a caught Exception into an :class:`AgentError`
    when it decides that retries + fallback have been exhausted. The
    error is then appended to the conversation as a
    ``role="tool"`` message so the model sees the same surface it
    would see from any other failing tool.
    """

    content: str
    tool_call_id: str = ""
    tool_name: str = ""
    is_error: bool = True


@dataclass(frozen=True)
class AgentResult:
    """End-state snapshot of a complete :meth:`Agent.run`.

    Attributes:
        messages: The conversation trace, including user / assistant /
            tool messages. Consumers typically render the last one.
        exit_reason: Human-readable reason the loop terminated; filled
            in by whichever :class:`ExitCondition` tripped, or
            ``"model_responded"`` on a clean finish.
        iterations: Number of outer iterations the loop completed.
        degraded: ``True`` if the agent ran in degraded mode at any
            point.
        retries_used: Total retries consumed from the
            :class:`RetryBudget`.
        tool_results: Structured record of every tool call result —
            kept for observability hooks and parity tests.
        final_text: Convenience extraction of the last assistant
            text (``""`` if the exit happened without a final message).
    """

    messages: list[Any]
    exit_reason: str
    iterations: int = 0
    degraded: bool = False
    retries_used: int = 0
    tool_results: tuple[Any, ...] = field(default_factory=tuple)
    final_text: str = ""


__all__ = ["AgentError", "AgentResult"]
