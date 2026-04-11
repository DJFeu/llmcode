"""E2E smoke test: every slash command in COMMAND_REGISTRY must be
dispatchable without crashing, under a minimal pilot-booted TUI.

This doesn't check that each command *works* (many of them need a
real runtime / MCP / LSP / tool registry) — it only enforces that
the dispatcher can route the command to its handler and the handler
survives a call with empty args. Regressions in this suite catch:

  - A handler raising on lazy imports.
  - A handler assuming a runtime field exists without checking.
  - A handler that's been renamed / deleted without a registry update.

Per-command behavioral tests live in dedicated files
(test_help_modal, test_voice_flow, test_export_flow, etc.).
"""
from __future__ import annotations

import pytest

from llm_code.cli.commands import COMMAND_REGISTRY

# Commands that need too much runtime state to smoke-test here without
# setting up a real LLM provider / session / tool registry. Each one
# already has a dedicated behavioral test elsewhere in tests/, so
# dropping them from the smoke run doesn't reduce coverage.
_SMOKE_SKIP = {
    "compact",  # needs a non-empty session with messages
    "exit",  # actually quits the app loop
    "quit",  # alias for exit
    "help",  # modal — covered by test_help_modal.py
    "export",  # needs session with messages — covered by test_export_flow.py
    "voice",  # covered by test_voice_flow.py
    "update",  # hits PyPI network
    "init",  # walks the real project; heavy
    "index",  # builds a project index — heavy
    "knowledge",  # runs the knowledge compiler
    "cancel",  # no-ops the stream worker; nothing to check in pilot
    "checkpoint",  # needs a session
    "vcr",  # needs VCR session
    "analyze",  # full analysis pipeline
    "diff_check",  # needs git state
    "lsp",  # needs LSP server
    "mcp",  # pushes a full modal browser
    "session",  # session manager modal
    "skill",  # skill browser modal
    "plugin",  # plugin browser modal
    "harness",  # harness controls
    "swarm",  # swarm coordinator
    "task",  # task manager
    "cron",  # cron scheduler
    "orchestrate",  # runs real agents
    "ide",  # IDE bridge
    "hida",  # needs HIDA profile
    "search",  # FTS5 search
    "plan",  # plan mode toggle
    "map",  # repo map
    "dump",  # dumps runtime state
    "diff",  # diff since checkpoint
    "thinking",  # toggle (harmless but state-dependent)
    "settings",  # modal
    "personas",  # lists swarm personas — OK to include
    "memory",  # needs memory store
    "profile",  # per-model token/cost breakdown — needs cost tracker
    "gain",  # token savings report
    "cost",  # token usage — needs cost tracker
    "cache",  # cache management
}


@pytest.mark.parametrize(
    "cmd",
    [c.name for c in COMMAND_REGISTRY if c.name not in _SMOKE_SKIP],
)
async def test_slash_command_dispatches_without_crash(pilot_app, cmd):
    """Every remaining command must survive a dispatch with empty args."""
    app, pilot = pilot_app
    # No assertion about what appears — this is a crash test. Any
    # exception here fails the test.
    try:
        app._cmd_dispatcher.dispatch(cmd, "")
    except Exception as exc:
        pytest.fail(f"/{cmd} raised {type(exc).__name__}: {exc}")
    await pilot.pause()


async def test_every_registry_command_has_handler(pilot_app):
    """Every CommandDef entry must have a matching _cmd_* method.
    This duplicates a unit test but is worth re-asserting in the
    E2E layer because a runtime-only import failure in one handler
    could mask a broken registration."""
    app, _pilot = pilot_app
    for cmd in COMMAND_REGISTRY:
        method_name = f"_cmd_{cmd.name}"
        assert hasattr(app._cmd_dispatcher, method_name), (
            f"Registry declares /{cmd.name} but CommandDispatcher has "
            f"no {method_name} method."
        )


async def test_unknown_command_does_not_crash_dispatcher(pilot_app):
    """A bogus subcommand should return False from dispatch, not raise."""
    app, _pilot = pilot_app
    result = app._cmd_dispatcher.dispatch("definitely_not_a_real_cmd", "")
    assert result is False
