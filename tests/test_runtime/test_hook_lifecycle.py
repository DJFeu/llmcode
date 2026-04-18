"""Tests for the async-friendly hook lifecycle registry (H1 — Sprint 3).

The existing ``HookRunner`` (llm_code/runtime/hooks.py) covers synchronous
shell + Python hooks well. This module adds a structured tracker for
async hooks the runtime kicks off but has to keep polling — think webhooks
or GPU-side validators that can exceed the current 10s subprocess timeout.
"""
from __future__ import annotations

import time

import pytest

from llm_code.runtime.hook_lifecycle import (
    HookInjection,
    HookLifecycleRegistry,
    HookPhase,
    PendingHook,
)


class TestHookPhase:
    def test_enum_values(self) -> None:
        assert HookPhase.PRE_TOOL_USE.value == "pre_tool_use"
        assert HookPhase.PERMISSION_REQUEST.value == "permission_request"
        assert HookPhase.POST_TOOL_USE.value == "post_tool_use"
        assert HookPhase.POST_TOOL_USE_FAILURE.value == "post_tool_use_failure"


class TestHookInjection:
    def test_defaults(self) -> None:
        inj = HookInjection()
        assert inj.denied is False
        assert inj.deny_reason == ""
        assert inj.updated_input is None
        assert inj.additional_context == ""

    def test_frozen(self) -> None:
        inj = HookInjection(denied=True, deny_reason="x")
        with pytest.raises(Exception):
            inj.denied = False  # type: ignore[misc]

    def test_is_approval(self) -> None:
        assert HookInjection().is_approval is True
        assert HookInjection(denied=True).is_approval is False


class TestHookLifecycleRegistry:
    def test_register_and_get_pending(self) -> None:
        reg = HookLifecycleRegistry()
        reg.register_pending(
            hook_id="h1", phase=HookPhase.PRE_TOOL_USE, timeout_s=5.0,
            context={"tool_name": "bash"},
        )
        pending = reg.pending()
        assert len(pending) == 1
        assert pending[0].hook_id == "h1"
        assert pending[0].phase is HookPhase.PRE_TOOL_USE
        assert pending[0].timeout_s == 5.0

    def test_complete_removes_from_pending(self) -> None:
        reg = HookLifecycleRegistry()
        reg.register_pending("h1", HookPhase.PRE_TOOL_USE, 5.0, {})
        inj = reg.complete("h1", HookInjection(additional_context="ok"))
        assert inj.additional_context == "ok"
        assert reg.pending() == ()

    def test_complete_unknown_hook_raises(self) -> None:
        reg = HookLifecycleRegistry()
        with pytest.raises(KeyError):
            reg.complete("nope", HookInjection())

    def test_double_complete_raises(self) -> None:
        reg = HookLifecycleRegistry()
        reg.register_pending("h1", HookPhase.PRE_TOOL_USE, 5.0, {})
        reg.complete("h1", HookInjection())
        with pytest.raises(KeyError):
            reg.complete("h1", HookInjection())

    def test_register_duplicate_hook_id_raises(self) -> None:
        """Duplicate hook_id means caller lost track of state — refuse."""
        reg = HookLifecycleRegistry()
        reg.register_pending("h1", HookPhase.PRE_TOOL_USE, 5.0, {})
        with pytest.raises(ValueError):
            reg.register_pending("h1", HookPhase.PRE_TOOL_USE, 5.0, {})

    def test_reap_timed_out(self) -> None:
        reg = HookLifecycleRegistry()
        reg.register_pending("slow", HookPhase.PRE_TOOL_USE, timeout_s=0.01, context={})
        reg.register_pending("fast", HookPhase.POST_TOOL_USE, timeout_s=60.0, context={})
        time.sleep(0.05)
        reaped = reg.reap_timed_out()
        assert len(reaped) == 1
        assert reaped[0].hook_id == "slow"
        # slow removed, fast still pending
        remaining = {p.hook_id for p in reg.pending()}
        assert remaining == {"fast"}

    def test_reaped_hooks_are_denied(self) -> None:
        reg = HookLifecycleRegistry()
        reg.register_pending("slow", HookPhase.PRE_TOOL_USE, timeout_s=0.01, context={"tool_name": "bash"})
        time.sleep(0.05)
        reg.reap_timed_out()
        # The caller gets an injection marking the hook denied with a
        # timeout reason — the runtime then knows to short-circuit the
        # tool call.
        inj = reg.outcome("slow")
        assert inj is not None
        assert inj.denied is True
        assert "timed out" in inj.deny_reason.lower()

    def test_outcome_before_complete_is_none(self) -> None:
        reg = HookLifecycleRegistry()
        reg.register_pending("h1", HookPhase.PRE_TOOL_USE, 5.0, {})
        assert reg.outcome("h1") is None

    def test_report_snapshot(self) -> None:
        reg = HookLifecycleRegistry()
        reg.register_pending("h1", HookPhase.PRE_TOOL_USE, 5.0, {"tool_name": "bash"})
        reg.register_pending("h2", HookPhase.POST_TOOL_USE, 10.0, {})
        reg.complete("h2", HookInjection())
        report = reg.report()
        assert report["pending_count"] == 1
        assert report["completed_count"] == 1
        assert report["by_phase"]["pre_tool_use"]["pending"] == 1
        assert report["by_phase"]["post_tool_use"]["completed"] == 1


class TestPendingHookLifecycle:
    """``PendingHook`` instances carry the age / status helpers the
    runtime needs for its ``--hook-debug`` dump."""

    def test_pending_age(self) -> None:
        hook = PendingHook(
            hook_id="h1",
            phase=HookPhase.PRE_TOOL_USE,
            started_at=time.monotonic() - 2.0,
            timeout_s=5.0,
            context={},
        )
        assert 1.9 <= hook.age_seconds <= 2.5

    def test_is_expired(self) -> None:
        old = PendingHook(
            hook_id="h1",
            phase=HookPhase.PRE_TOOL_USE,
            started_at=time.monotonic() - 10.0,
            timeout_s=5.0,
            context={},
        )
        fresh = PendingHook(
            hook_id="h2",
            phase=HookPhase.PRE_TOOL_USE,
            started_at=time.monotonic(),
            timeout_s=5.0,
            context={},
        )
        assert old.is_expired is True
        assert fresh.is_expired is False
