"""Tests for HookRunner <-> HookLifecycleRegistry wiring (H1 wire — Sprint 3).

The existing HookRunner is stable; we don't change its synchronous
fire path. The wire simply lets callers attach a HookLifecycleRegistry
and read it back through a public property + debug_report() so the
async-hook path (registered in a follow-up) has a home.
"""
from __future__ import annotations

from llm_code.runtime.hook_lifecycle import HookLifecycleRegistry, HookPhase
from llm_code.runtime.hooks import HookRunner


class TestHookRunnerLifecycleWire:
    def test_default_lifecycle_is_none(self) -> None:
        runner = HookRunner()
        assert runner.lifecycle is None

    def test_wire_lifecycle_attaches_registry(self) -> None:
        runner = HookRunner()
        reg = HookLifecycleRegistry()
        runner.wire_lifecycle(reg)
        assert runner.lifecycle is reg

    def test_debug_report_without_lifecycle(self) -> None:
        runner = HookRunner()
        report = runner.debug_report()
        assert "subscriber_event_count" in report
        assert "lifecycle" not in report or report["lifecycle"] is None

    def test_debug_report_includes_lifecycle_when_wired(self) -> None:
        runner = HookRunner()
        reg = HookLifecycleRegistry()
        reg.register_pending(
            "h1", HookPhase.PRE_TOOL_USE, timeout_s=5.0,
            context={"tool_name": "bash"},
        )
        runner.wire_lifecycle(reg)
        report = runner.debug_report()
        assert report["lifecycle"]["pending_count"] == 1

    def test_debug_report_counts_subscribers(self) -> None:
        runner = HookRunner()

        def noop(event, context):  # noqa: ARG001
            return None

        runner.subscribe("pre_tool_use", noop)
        runner.subscribe("pre_tool_use", noop)
        runner.subscribe("post_tool_use", noop)
        report = runner.debug_report()
        # subscriber_event_count: how many distinct event names have subscribers
        assert report["subscriber_event_count"] == 2
        # subscriber_total_count: sum across all events
        assert report["subscriber_total_count"] == 3

    def test_wire_lifecycle_can_be_called_twice_to_replace(self) -> None:
        runner = HookRunner()
        reg1 = HookLifecycleRegistry()
        reg2 = HookLifecycleRegistry()
        runner.wire_lifecycle(reg1)
        runner.wire_lifecycle(reg2)
        assert runner.lifecycle is reg2
