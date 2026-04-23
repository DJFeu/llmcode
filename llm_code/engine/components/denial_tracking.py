"""DenialTrackingComponent — record denied tool calls on the session tracker.

Wraps :class:`llm_code.runtime.permission_denial_tracker.PermissionDenialTracker`
so that any ``allowed=False`` decision emitted by an upstream
``PermissionCheckComponent`` / hook / safety check is persisted on the
same append-only log used by ``/diagnose`` and the enterprise audit
pipeline.

Outputs
-------
- ``proceed``: mirrors ``allowed`` — downstream gate stages branch on it
  to decide whether to run :class:`ToolExecutorComponent`.
- ``denial_history``: immutable tuple snapshot of the tracker. Attached
  as a Pipeline-level state write so parity tests (and, later, the Agent
  loop) can observe the running history without reaching into the
  Component instance.

State: declares ``state_writes("denial_history")`` so the Pipeline
validator catches two writers on the same key at build time.

Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-pipeline-dag.md Task 2.7 Step 1
"""
from __future__ import annotations

from typing import Any

from llm_code.engine.component import component, output_types, state_writes
from llm_code.runtime.permission_denial_tracker import (
    DeniedToolCall,
    DenialSource,
    PermissionDenialTracker,
)


@component
@output_types(proceed=bool, denial_history=tuple)
@state_writes("denial_history")
class DenialTrackingComponent:
    """Record and expose permission denials for a Pipeline session.

    Construct with an optional external :class:`PermissionDenialTracker`
    so the ``ConversationRuntime`` can share its session-scoped instance.
    When no tracker is supplied, the Component owns a fresh one for
    isolated tests / stateless flows.
    """

    def __init__(self, tracker: PermissionDenialTracker | None = None) -> None:
        self._tracker = tracker or PermissionDenialTracker()

    @property
    def tracker(self) -> PermissionDenialTracker:
        """Exposed so parity tests can assert on the underlying tracker."""
        return self._tracker

    def run(
        self,
        allowed: bool,
        reason: str,
        tool_name: str,
        tool_use_id: str,
        tool_args: dict,
    ) -> dict[str, Any]:
        """Record a denial when ``allowed`` is False; always surface history."""
        if not allowed:
            self._tracker.record(
                tool_name=tool_name,
                tool_use_id=tool_use_id,
                input=dict(tool_args),  # defensive copy
                reason=reason or "denied by upstream component",
                source=DenialSource.POLICY,
            )
        history: tuple[DeniedToolCall, ...] = self._tracker.entries()
        return {"proceed": allowed, "denial_history": history}
