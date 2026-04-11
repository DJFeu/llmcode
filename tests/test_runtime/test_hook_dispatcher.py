"""Tests for HookDispatcher (Phase 2.1 extraction)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def test_hook_dispatcher_fires_through_runner() -> None:
    """HookDispatcher.fire delegates to HookRunner.fire with the context."""
    from llm_code.runtime.hook_dispatcher import HookDispatcher

    runner = MagicMock()
    dispatcher = HookDispatcher(runner)

    dispatcher.fire("post_tool_use", {"tool_name": "bash"})

    runner.fire.assert_called_once_with("post_tool_use", {"tool_name": "bash"})


def test_hook_dispatcher_defaults_missing_context_to_empty_dict() -> None:
    from llm_code.runtime.hook_dispatcher import HookDispatcher

    runner = MagicMock()
    dispatcher = HookDispatcher(runner)

    dispatcher.fire("session_compact")

    runner.fire.assert_called_once_with("session_compact", {})


def test_hook_dispatcher_noop_without_runner() -> None:
    """A None runner must never raise — the conversation may run without hooks."""
    from llm_code.runtime.hook_dispatcher import HookDispatcher

    dispatcher = HookDispatcher(None)
    # Must not raise.
    dispatcher.fire("post_tool_use", {"tool_name": "bash"})


def test_hook_dispatcher_swallows_runner_exceptions() -> None:
    """A failing hook must never break the conversation loop."""
    from llm_code.runtime.hook_dispatcher import HookDispatcher

    runner = MagicMock()
    runner.fire.side_effect = RuntimeError("bad hook")
    dispatcher = HookDispatcher(runner)

    # Must not raise.
    dispatcher.fire("post_tool_use", {"tool_name": "bash"})


def test_hook_dispatcher_skips_runner_without_fire_method() -> None:
    """Legacy runners without `.fire` shouldn't crash — the old `_fire_hook`
    also guarded with `hasattr`, keep parity."""
    from llm_code.runtime.hook_dispatcher import HookDispatcher

    class _LegacyRunner:
        pass

    dispatcher = HookDispatcher(_LegacyRunner())
    dispatcher.fire("post_tool_use")  # must not raise


def test_conversation_runtime_uses_hook_dispatcher(monkeypatch: pytest.MonkeyPatch) -> None:
    """ConversationRuntime._fire_hook should delegate through HookDispatcher."""
    from llm_code.runtime.conversation import ConversationRuntime
    from llm_code.runtime.hook_dispatcher import HookDispatcher

    # Avoid heavyweight construction; patch the dispatcher attribute onto a
    # minimal object that borrows the method.
    runner = MagicMock()
    dispatcher = HookDispatcher(runner)

    class _Stub:
        _hook_dispatcher = dispatcher
        _hooks = runner
        _fire_hook = ConversationRuntime._fire_hook

    _Stub()._fire_hook("pre_compact", {"reason": "manual"})
    runner.fire.assert_called_once_with("pre_compact", {"reason": "manual"})
