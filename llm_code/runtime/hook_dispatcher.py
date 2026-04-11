"""Hook event dispatch extracted from ``ConversationRuntime``.

Phase 2.1 of the 2026-04-11 architecture refactor: the conversation runtime
used to own ``_fire_hook``, which combined "is there a runner?", "does the
runner support the generic fire API?", and "swallow hook errors" into a
single inline guard. Splitting that out into a dedicated ``HookDispatcher``
keeps ``ConversationRuntime`` focused on the turn lifecycle and gives the
hook plumbing its own testable seam.

The dispatcher is intentionally thin: it only takes a ``HookRunner`` (or
``None``) and forwards generic ``fire(event, context)`` calls. Specialized
paths such as ``pre_tool_use`` and ``fire_python`` stay on the runner itself
because they return typed outcomes the callers already inspect — wrapping
those would buy nothing.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from llm_code.logging import get_logger

if TYPE_CHECKING:
    from llm_code.runtime.hooks import HookRunner

logger = get_logger(__name__)


class HookDispatcher:
    """Thin wrapper that forwards generic hook events to a ``HookRunner``.

    * ``runner is None`` → all ``fire`` calls are no-ops.
    * ``runner`` without a ``fire`` method → skipped (legacy runners).
    * Exceptions raised by hooks are logged and swallowed, preserving the
      previous ``_fire_hook`` semantics so a misbehaving hook can never
      break the conversation loop.
    """

    def __init__(self, runner: "HookRunner | None") -> None:
        self._runner = runner

    def fire(self, event: str, context: dict[str, Any] | None = None) -> Any:
        """Fire ``event`` with ``context`` on the underlying runner.

        Returns whatever the runner's ``fire`` returns (typically a
        ``HookOutcome``), or ``None`` when there is nothing to dispatch.
        """
        runner = self._runner
        if runner is None:
            return None
        fire = getattr(runner, "fire", None)
        if fire is None:
            return None
        try:
            return fire(event, context or {})
        except Exception:
            logger.warning("Hook %s failed", event, exc_info=True)
            return None
