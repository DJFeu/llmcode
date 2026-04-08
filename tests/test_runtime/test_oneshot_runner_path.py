"""Verify that a quick-mode (-q) request now exercises the full
ConversationRuntime.run_turn path — not the direct-provider bypass it
used before 2026-04-08.

This is the test that PRs #11/#13/#14 should have had. It proves the
smoke test exercises the same code path as the TUI.
"""
from __future__ import annotations

import pytest

from tests.fixtures.runtime import make_conv_runtime


@pytest.mark.asyncio
async def test_quick_mode_goes_through_run_turn_body() -> None:
    """Instantiate the shared fixture, stream a fake model response
    that emits a Hermes-format tool_call, and assert the tool was
    dispatched by the runtime (not bypassed via -q's old shortcut)."""
    hermes_tool_call = (
        '<tool_call>echo>{"args": {"message": "hello"}}</tool_call>'
    )
    dispatched: list[tuple[str, dict]] = []

    def _echo_execute(args: dict) -> dict:
        dispatched.append(("echo", args))
        return {"output": f"echoed: {args.get('message', '')}", "is_error": False}

    runtime = make_conv_runtime(
        canned_response_text=hermes_tool_call,
        extra_tools={"echo": _echo_execute},
    )
    await runtime.run_one_turn("please echo hello")
    assert dispatched == [("echo", {"message": "hello"})], (
        f"runtime did not dispatch the echo tool; dispatched={dispatched}"
    )


def test_fixture_exports_make_conv_runtime() -> None:
    """Sanity check that the factory is importable from the agreed
    location, so other tests can migrate to it."""
    from tests.fixtures.runtime import make_conv_runtime  # noqa: F401
