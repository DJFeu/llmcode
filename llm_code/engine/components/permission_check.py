"""PermissionCheckComponent — permission gate as a Pipeline stage.

Borrowed shape: none (native v12 wiring around existing
:class:`llm_code.runtime.permissions.PermissionPolicy`).

The Component consumes ``(tool_name, tool_args, is_read_only)`` and emits
``(allowed, reason)``. The downstream :class:`DenialTrackingComponent`
uses the pair to decide whether to record a denial entry; the
:class:`RateLimiterComponent` uses ``allowed`` to short-circuit when the
call is already denied.

Design notes
------------
- The wrapped :class:`PermissionPolicy` stays unchanged; we map its
  :class:`PermissionOutcome` enum onto a boolean+reason pair. Any
  non-``ALLOW`` outcome (``DENY``, ``NEED_PROMPT``, ``NEED_PLAN``) is
  reported as ``allowed=False`` — the Pipeline does not own UI prompts;
  surfacing the prompt is the caller's responsibility, the Component
  itself is side-effect-free.
- ``tool_args`` is declared as ``dict`` and is forwarded verbatim to the
  policy (not consumed for the decision itself today, but retained so
  future denial-pattern rules can match on args without signature
  churn).
- ``default_required`` lets callers override the level the policy checks
  against when the real tool object isn't available (tests + parity
  runners); default is :attr:`PermissionLevel.WORKSPACE_WRITE` which is
  the conservative middle ground.

Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-pipeline-dag.md Task 2.6
"""
from __future__ import annotations

from typing import Any

from llm_code.engine.component import component, output_types, state_reads
from llm_code.runtime.permissions import (
    PermissionMode,
    PermissionOutcome,
    PermissionPolicy,
)
from llm_code.tools.base import PermissionLevel


@component
@output_types(allowed=bool, reason=str)
@state_reads("mode")
class PermissionCheckComponent:
    """Gate a tool call through a :class:`PermissionPolicy`.

    Args:
        policy: The :class:`PermissionPolicy` to consult. Held by
            reference so dynamic policy updates (e.g. ``allow_tool``)
            take effect without re-wiring the Pipeline.
        default_required: Permission level used for the authorize()
            call when the caller does not pass a live :class:`Tool`.
            Parity tests and synthetic fixtures set this; production
            flows get the level from the tool object.

    Inputs:
        tool_name: Registered name of the tool to authorize.
        tool_args: Validated argument dict, forwarded verbatim.
        is_read_only: Outcome of the tool's own ``is_read_only`` check.
            When ``True`` the component downgrades the effective level
            to :attr:`PermissionLevel.READ_ONLY` before authorisation.

    Outputs:
        allowed: ``True`` iff the policy returned
            :attr:`PermissionOutcome.ALLOW`.
        reason: Empty string on allow; human-readable rationale
            otherwise (useful for :class:`DenialTrackingComponent` and
            observability spans).
    """

    def __init__(
        self,
        policy: PermissionPolicy,
        *,
        default_required: PermissionLevel = PermissionLevel.WORKSPACE_WRITE,
    ) -> None:
        self._policy = policy
        self._default_required = default_required

    def run(
        self,
        tool_name: str,
        tool_args: dict,
        is_read_only: bool,
    ) -> dict[str, Any]:
        """Authorize ``tool_name`` and return ``(allowed, reason)``."""
        # ``tool_args`` is not read today but kept in the signature so
        # Socket introspection stays stable for future arg-aware rules.
        _ = tool_args

        effective = (
            PermissionLevel.READ_ONLY if is_read_only else self._default_required
        )
        outcome = self._policy.authorize(
            tool_name,
            self._default_required,
            effective_level=effective,
        )
        if outcome is PermissionOutcome.ALLOW:
            return {"allowed": True, "reason": ""}
        return {"allowed": False, "reason": _reason_for(outcome, self._policy, tool_name)}


def _reason_for(
    outcome: PermissionOutcome,
    policy: PermissionPolicy,
    tool_name: str,
) -> str:
    """Build a stable, grep-friendly reason string for each denial flavour."""
    if outcome is PermissionOutcome.DENY:
        # Distinguish deny-list from mode-level denial so observability
        # can split the two without parsing the text.
        if tool_name in getattr(policy, "_deny_tools", frozenset()):
            return f"denied by deny_tools list: {tool_name!r}"
        mode = policy.mode
        if mode is PermissionMode.READ_ONLY:
            return (
                f"denied by mode read_only: {tool_name!r} exceeds "
                "read-only permission level"
            )
        return f"denied by permission policy ({mode.value})"
    if outcome is PermissionOutcome.NEED_PROMPT:
        return f"needs user prompt in mode {policy.mode.value}"
    if outcome is PermissionOutcome.NEED_PLAN:
        return "plan mode active — switch to build mode to execute mutating tools"
    return "denied"
