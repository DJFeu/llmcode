"""Built-in :class:`ExitCondition` implementations.

An exit condition is a predicate over the live :class:`State` dict. The
Agent consults the full list every iteration; the first positive match
wins. Conditions are intentionally additive: replacing the ad-hoc
``if iteration > N`` branches in ``conversation.py`` with composable
objects means a user can replace or extend the exit behaviour without
touching the core loop.

Spec: docs/superpowers/specs/2026-04-21-llm-code-v12-haystack-borrow-design.md §5.3
Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-agent-loop-control.md Task 3.5
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Iterable

from llm_code.engine.policies import ExitCondition
from llm_code.engine.state import State


class MaxStepsReached:
    """Exit when ``state["iteration"]`` hits ``cap``.

    Also emits a one-shot reminder ``warning_offset`` iterations before
    the cap so the model can wrap up gracefully instead of being cut
    off mid-tool-call. The reminder text is deliberately simple —
    callers that want the full Jinja-templated reminder can override
    :meth:`warning_reminder`.
    """

    def __init__(self, cap: int = 50, warning_offset: int = 5) -> None:
        if cap < 1:
            raise ValueError("cap must be >= 1")
        if warning_offset < 0:
            raise ValueError("warning_offset must be >= 0")
        self._cap = cap
        self._warn_at = max(0, cap - warning_offset)

    @property
    def cap(self) -> int:
        return self._cap

    def should_exit(self, state: State) -> tuple[bool, str]:
        i = int(state.get("iteration", 0))
        if i >= self._cap:
            return True, f"max_steps reached ({i}/{self._cap})"
        return False, ""

    def warning_reminder(self, state: State) -> str | None:
        """One-shot reminder emitted at ``cap - warning_offset``.

        Returns the reminder string, or ``None`` if the iteration
        pointer is not at the warning step. The agent injects the
        non-``None`` return as a ``role=system`` message on the next
        turn.
        """
        i = int(state.get("iteration", 0))
        if i != self._warn_at:
            return None
        remaining = self._cap - i
        return (
            f"You have used {i} of {self._cap} steps. "
            f"{remaining} steps remain — please wrap up soon."
        )


class NoProgress:
    """Exit when the last ``window`` iterations produced identical tool
    calls.

    "Identical" is measured by hashing the sorted-JSON representation
    of each tool call. Repeating the same call with the same args is a
    strong signal the agent is spinning; cutting out avoids burning the
    whole step budget on a loop.
    """

    def __init__(self, window: int = 3) -> None:
        if window < 2:
            raise ValueError("window must be >= 2")
        self._window = window

    def should_exit(self, state: State) -> tuple[bool, str]:
        calls = list(state.get("tool_calls", []))
        if len(calls) < self._window:
            return False, ""
        recent = calls[-self._window:]
        fingerprint = {self._hash(c) for c in recent}
        if len(fingerprint) == 1:
            return (
                True,
                f"no progress: {self._window} identical tool calls in a row",
            )
        return False, ""

    @staticmethod
    def _hash(call: Any) -> str:
        """Stable hash of a tool call.

        We extract ``tool_name`` / ``name`` and ``args`` fields when
        present, and fall back to :func:`repr` when the object is
        opaque. Sorting JSON keys makes the hash invariant to dict
        ordering.
        """
        name = getattr(call, "tool_name", None) or getattr(call, "name", None)
        args = getattr(call, "args", None)
        if name is None and isinstance(call, dict):
            name = call.get("tool_name") or call.get("name")
            args = call.get("args", args)
        try:
            payload = json.dumps(
                {"name": name, "args": args}, sort_keys=True, default=repr
            )
        except (TypeError, ValueError):
            payload = repr((name, args))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ExplicitExitTool:
    """Exit when the agent calls a sentinel tool.

    The default sentinel is ``exit_agent``. When the last entry in
    ``state["tool_calls"]`` matches, we exit immediately — no need to
    execute the tool, the intent was the signal.
    """

    TOOL_NAME = "exit_agent"

    def __init__(self, tool_name: str | None = None) -> None:
        self._name = tool_name or self.TOOL_NAME

    @property
    def tool_name(self) -> str:
        return self._name

    def should_exit(self, state: State) -> tuple[bool, str]:
        calls = state.get("tool_calls", [])
        if not calls:
            return False, ""
        last = calls[-1]
        last_name = (
            getattr(last, "tool_name", None)
            or getattr(last, "name", None)
            or (last.get("tool_name") if isinstance(last, dict) else None)
            or (last.get("name") if isinstance(last, dict) else None)
        )
        if last_name == self._name:
            return True, f"explicit exit via {self._name}"
        return False, ""


class DenialThreshold:
    """Exit when permission denials pile up.

    ``state["denial_history"]`` is a list of tool calls that were
    denied. When ``>= threshold`` entries appear in the last ``window``
    iterations, we bail — continuing would just produce more denials
    without user intervention.
    """

    def __init__(self, threshold: int = 3, window: int = 10) -> None:
        if threshold < 1:
            raise ValueError("threshold must be >= 1")
        if window < threshold:
            raise ValueError("window must be >= threshold")
        self._threshold = threshold
        self._window = window

    def should_exit(self, state: State) -> tuple[bool, str]:
        denials = list(state.get("denial_history", []))
        if len(denials) < self._threshold:
            return False, ""
        recent = denials[-self._window:]
        if len(recent) >= self._threshold:
            return (
                True,
                f"{len(recent)} permission denials in the last {self._window} events",
            )
        return False, ""


class BudgetExhausted:
    """Exit when a budget is 100% used.

    Uses the same ``usage_fn`` indirection as
    :class:`~llm_code.engine.policies.degraded.BudgetDegraded` — pass a
    callable that returns ``[0.0, 1.0]``.
    """

    def __init__(
        self,
        usage_fn: Callable[[State], float],
        threshold: float = 1.0,
    ) -> None:
        if not 0.0 < threshold <= 1.0:
            raise ValueError("threshold must be in (0, 1]")
        self._usage = usage_fn
        self._threshold = threshold

    def should_exit(self, state: State) -> tuple[bool, str]:
        try:
            used = float(self._usage(state))
        except Exception as exc:  # noqa: BLE001 - defensive
            return False, f"usage_fn raised: {exc}"
        if used >= self._threshold:
            return True, f"budget exhausted ({used:.0%})"
        return False, ""


class CompositeExit:
    """OR-combine multiple exit conditions; first match wins.

    Exposes :attr:`members` so the Agent can walk the list to collect
    side-effects (e.g. ``warning_reminder`` calls on
    :class:`MaxStepsReached`).
    """

    def __init__(self, members: Iterable[ExitCondition]) -> None:
        self._members = tuple(members)

    @property
    def members(self) -> tuple[ExitCondition, ...]:
        return self._members

    def should_exit(self, state: State) -> tuple[bool, str]:
        for m in self._members:
            decision = m.should_exit(state)
            if decision[0]:
                return decision
        return False, ""


__all__ = [
    "BudgetExhausted",
    "CompositeExit",
    "DenialThreshold",
    "ExplicitExitTool",
    "MaxStepsReached",
    "NoProgress",
]
