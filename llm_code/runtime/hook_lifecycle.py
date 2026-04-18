"""Async-friendly hook lifecycle registry (H1 — Sprint 3).

The existing :class:`llm_code.runtime.hooks.HookRunner` runs shell +
Python hooks synchronously with a per-hook subprocess timeout. That
covers 80% of cases but breaks down when a hook has to wait on an
external resource (webhook, GPU validator, multi-stage approval). This
module adds a lightweight registry for hooks the runtime kicks off
and polls.

Design goals:

    * Track in-flight hooks by id so ``--hook-debug`` can dump their
      phase / age / timeout.
    * Age-based reaping so a slow/hung hook can never wedge the turn
      forever; reaped hooks become an :class:`HookInjection` carrying
      ``denied=True`` so the tool caller short-circuits correctly.
    * Structured injection (``updated_input`` / ``additional_context``)
      so the ``isinstance(hook_result, dict)`` dance in tool_pipeline
      has a proper home to migrate to later.

The registry is pure data + the ``time`` module — no subprocess, no
asyncio. Integration with ``HookRunner`` lives in a follow-up so this
commit stays self-contained.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class HookPhase(Enum):
    """Phases of a tool call around which hooks can fire."""
    PRE_TOOL_USE = "pre_tool_use"
    PERMISSION_REQUEST = "permission_request"
    POST_TOOL_USE = "post_tool_use"
    POST_TOOL_USE_FAILURE = "post_tool_use_failure"


@dataclass(frozen=True)
class HookInjection:
    """Structured hook outcome — replaces the ad-hoc dict contract.

    Fields:

        * ``denied`` — when True the caller must short-circuit the
          tool call. ``deny_reason`` is surfaced in the error message.
        * ``updated_input`` — rewritten tool args. When not None, the
          tool executes with this dict instead of the original input.
        * ``additional_context`` — extra text appended to the tool
          result, mirroring ``HookOutcome.extra_output`` from the
          existing runner.
    """
    denied: bool = False
    deny_reason: str = ""
    updated_input: dict[str, Any] | None = None
    additional_context: str = ""

    @property
    def is_approval(self) -> bool:
        return not self.denied


@dataclass(frozen=True)
class PendingHook:
    """An in-flight hook the registry is tracking."""
    hook_id: str
    phase: HookPhase
    started_at: float           # time.monotonic() at register time
    timeout_s: float
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def age_seconds(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def is_expired(self) -> bool:
        return self.age_seconds > self.timeout_s


class HookLifecycleRegistry:
    """Tracks pending hooks + their eventual :class:`HookInjection`.

    The runtime registers a hook before kicking off the async work,
    then calls :meth:`complete` once the work finishes (or
    :meth:`reap_timed_out` to auto-fail anything past its deadline).

    ``--hook-debug`` consumes :meth:`pending` / :meth:`report` to dump
    what's currently outstanding.
    """

    def __init__(self) -> None:
        self._pending: dict[str, PendingHook] = {}
        self._completed: dict[str, HookInjection] = {}
        # Phase is captured at complete() time so report() can compute
        # per-phase counts without re-inspecting pending entries.
        self._completed_phases: dict[str, HookPhase] = {}

    # ----- registration -----------------------------------------------

    def register_pending(
        self,
        hook_id: str,
        phase: HookPhase,
        timeout_s: float,
        context: dict[str, Any],
    ) -> PendingHook:
        if hook_id in self._pending or hook_id in self._completed:
            raise ValueError(f"hook_id already registered: {hook_id!r}")
        hook = PendingHook(
            hook_id=hook_id,
            phase=phase,
            started_at=time.monotonic(),
            timeout_s=timeout_s,
            context=dict(context),
        )
        self._pending[hook_id] = hook
        return hook

    # ----- completion -------------------------------------------------

    def complete(self, hook_id: str, injection: HookInjection) -> HookInjection:
        if hook_id not in self._pending:
            raise KeyError(f"no pending hook with id {hook_id!r}")
        phase = self._pending[hook_id].phase
        del self._pending[hook_id]
        self._completed[hook_id] = injection
        self._completed_phases[hook_id] = phase
        return injection

    def outcome(self, hook_id: str) -> HookInjection | None:
        return self._completed.get(hook_id)

    # ----- reaping ----------------------------------------------------

    def reap_timed_out(self) -> tuple[PendingHook, ...]:
        """Move every expired pending hook into completed as a deny.

        Returns the reaped :class:`PendingHook` entries for logging.
        """
        expired = tuple(h for h in self._pending.values() if h.is_expired)
        for hook in expired:
            del self._pending[hook.hook_id]
            self._completed[hook.hook_id] = HookInjection(
                denied=True,
                deny_reason=(
                    f"hook {hook.hook_id!r} timed out after "
                    f"{hook.age_seconds:.2f}s (limit {hook.timeout_s}s)"
                ),
            )
            self._completed_phases[hook.hook_id] = hook.phase
        return expired

    # ----- introspection ----------------------------------------------

    def pending(self) -> tuple[PendingHook, ...]:
        return tuple(self._pending.values())

    def report(self) -> dict[str, Any]:
        """JSON-safe summary for ``--hook-debug`` / diagnose output."""
        by_phase: dict[str, dict[str, int]] = {
            phase.value: {"pending": 0, "completed": 0} for phase in HookPhase
        }
        for hook in self._pending.values():
            by_phase[hook.phase.value]["pending"] += 1
        for phase in self._completed_phases.values():
            by_phase[phase.value]["completed"] += 1
        return {
            "pending_count": len(self._pending),
            "completed_count": len(self._completed),
            "by_phase": by_phase,
        }
