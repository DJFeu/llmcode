# llm_code/tui/runtime_init.py
"""RuntimeInitializer — thin adapter over ``runtime/app_state.py``.

The full initialization logic (~440 lines, ~30 subsystems) used to live
in this file as the body of ``initialize()``. M10.3 moved that body to
``llm_code.runtime.app_state.AppState.from_config`` so v2.0.0's REPL
backend can build the same state graph without instantiating
``LLMCodeTUI``.

This adapter keeps the legacy TUI fully functional during the M10 →
M11 transition: it calls the factory, copies the resulting subsystem
references back onto ``LLMCodeTUI``, and then performs the three
TUI-specific wire-ups that AppState deliberately does not touch:

1. Construct and attach ``TextualDialogs``.
2. Rebuild runtime with ``dialogs=`` injected (so the TUI's modal
   prompts work the same as before).
3. Install the MCP approval event sink so non-root server spawns
   surface an inline widget.

M11 deletes ``tui/`` entirely; at that point this adapter and its
field-copy loop go with it, and ``cli/main.py`` calls
``AppState.from_config`` directly.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from llm_code.logging import get_logger

if TYPE_CHECKING:
    from llm_code.tui.app import LLMCodeTUI

logger = get_logger(__name__)


# Fields the adapter copies from the returned AppState onto the owning
# LLMCodeTUI instance. Every legacy attribute name on the left is the
# v1.x private-underscore form; the AppState field name on the right
# is the public dataclass field. Anything the legacy TUI reads via
# ``self._xxx`` must appear here.
_FIELD_MAP: tuple[tuple[str, str], ...] = (
    ("_cost_tracker", "cost_tracker"),
    ("_tool_reg", "tool_reg"),
    ("_deferred_tool_manager", "deferred_tool_manager"),
    ("_checkpoint_mgr", "checkpoint_mgr"),
    ("_skills", "skills"),
    ("_memory", "memory"),
    ("_typed_memory", "typed_memory"),
    ("_cron_storage", "cron_storage"),
    ("_swarm_manager", "swarm_manager"),
    ("_task_manager", "task_manager"),
    ("_ide_bridge", "ide_bridge"),
    ("_lsp_manager", "lsp_manager"),
    ("_project_index", "project_index"),
    ("_user_agent_roles", "user_agent_roles"),
    ("_runtime", "runtime"),
)


class RuntimeInitializer:
    """Thin TUI-side adapter around :class:`AppState.from_config`.

    Stores a back-reference to the owning ``LLMCodeTUI`` so it can
    copy state fields back after the factory runs.
    """

    def __init__(self, app: "LLMCodeTUI") -> None:
        self._app = app

    def initialize(self) -> None:
        """Delegate to ``AppState.from_config`` and wire TUI-specific extras.

        After this returns, ``self._app`` has the same ~30 private
        subsystem attributes populated as it did in v1.x. The only
        observable difference is that runtime was constructed with
        ``dialogs=TextualDialogs(app)`` (same as before), and the MCP
        event sink has been installed.
        """
        if self._app._config is None:
            logger.warning("No config provided; runtime will not be initialized.")
            return

        # ── Phase 1: build the AppState via the shared factory ────
        from llm_code.runtime.app_state import AppState
        state = AppState.from_config(
            self._app._config,
            cwd=self._app._cwd,
            budget=self._app._budget,
        )

        # Copy subsystem references back onto the legacy TUI. Any
        # attribute not in _FIELD_MAP stays on its existing value
        # (e.g. the input/output token counters and other live-state
        # fields that LLMCodeTUI already initialized to sensible
        # defaults in its own __init__).
        for legacy_attr, state_field in _FIELD_MAP:
            setattr(self._app, legacy_attr, getattr(state, state_field))

        # ── Phase 2: TUI-specific wire-up ────────────────────────
        # AppState.from_config deliberately skips these three steps
        # because they're Textual-specific. The TUI adapter adds them
        # on top so the legacy app keeps working bit-for-bit.

        # 2a. TextualDialogs
        try:
            from llm_code.tui.dialogs.textual_backend import TextualDialogs
            self._app._dialogs = TextualDialogs(self._app)
        except Exception as exc:
            logger.warning("TextualDialogs init failed: %r", exc)
            self._app._dialogs = None

        # 2b. Rebuild runtime with dialogs attached. AppState.from_config
        # already built a runtime with dialogs=None, but the legacy TUI
        # expects to use TextualDialogs for its modal prompts. The
        # cheapest way to keep bit-for-bit TUI behavior without
        # duplicating the ConversationRuntime constructor is to set
        # the ``_dialogs`` attribute on the existing runtime — runtime
        # reads it lazily through the ``dialogs`` property.
        if self._app._runtime is not None and self._app._dialogs is not None:
            self._app._runtime._dialogs = self._app._dialogs

        # 2c. MCP approval event sink — non-root MCP server spawns
        # surface inline approval widgets via this callback on the TUI.
        try:
            if self._app._runtime is not None:
                self._app._runtime.set_mcp_event_sink(
                    self._app._on_mcp_approval_event,
                )
        except Exception as exc:
            logger.warning("MCP approval sink install failed: %r", exc)
