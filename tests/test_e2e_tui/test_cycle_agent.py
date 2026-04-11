"""E2E: Shift+Tab / Ctrl+Y cycle between build / plan / suggest agents."""
from __future__ import annotations

from unittest.mock import MagicMock


async def _install_mock_runtime(app):
    """Wire a minimal runtime with a mutable `_permissions._mode` field
    so action_cycle_agent can flip it and the test can inspect it."""
    from llm_code.runtime.permissions import PermissionMode

    policy = MagicMock()
    policy._mode = PermissionMode.WORKSPACE_WRITE

    runtime = MagicMock()
    runtime._permissions = policy
    app._runtime = runtime
    return policy


async def test_cycle_agent_progresses_through_three_modes(pilot_app):
    """Call action_cycle_agent three times and verify we hit each of
    build / plan / suggest / build again, and that the status bar's
    reactive fields reflect the switch each step."""
    from llm_code.runtime.permissions import PermissionMode
    from llm_code.tui.status_bar import StatusBar

    app, pilot = pilot_app
    policy = await _install_mock_runtime(app)
    status = app.query_one(StatusBar)

    # Step 1: build → plan.
    app.action_cycle_agent()
    await pilot.pause()
    assert policy._mode == PermissionMode.PLAN
    assert status.permission_mode == "plan"
    assert status.plan_mode == "PLAN"

    # Step 2: plan → suggest.
    app.action_cycle_agent()
    await pilot.pause()
    assert policy._mode == PermissionMode.PROMPT
    assert status.permission_mode == "suggest"

    # Step 3: suggest → build.
    app.action_cycle_agent()
    await pilot.pause()
    assert policy._mode == PermissionMode.WORKSPACE_WRITE
    assert status.permission_mode == "build"
    # BUILD mode clears the plan_mode label.
    assert status.plan_mode == ""


async def test_shift_tab_hotkey_invokes_action(pilot_app):
    """Shift+Tab should route to the cycle_agent action binding
    declared on the App. Smoke-check via pilot.press — the real
    keybinding registry must resolve Shift+Tab correctly."""
    from llm_code.runtime.permissions import PermissionMode

    app, pilot = pilot_app
    policy = await _install_mock_runtime(app)
    initial = policy._mode
    assert initial == PermissionMode.WORKSPACE_WRITE

    await pilot.press("shift+tab")
    await pilot.pause()
    # Must have advanced to PLAN.
    assert policy._mode == PermissionMode.PLAN


async def test_ctrl_y_alternate_hotkey(pilot_app):
    """Ctrl+Y is the alternate binding for cycle_agent per app.py:274."""
    from llm_code.runtime.permissions import PermissionMode

    app, pilot = pilot_app
    policy = await _install_mock_runtime(app)

    await pilot.press("ctrl+y")
    await pilot.pause()
    assert policy._mode == PermissionMode.PLAN


async def test_cycle_agent_noop_when_runtime_missing(pilot_app):
    """Pressing the hotkey with no runtime attached must not crash —
    the action handler early-returns."""
    app, pilot = pilot_app
    app._runtime = None
    # Must not raise.
    app.action_cycle_agent()
    await pilot.pause()
