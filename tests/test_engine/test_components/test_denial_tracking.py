"""Tests for :class:`DenialTrackingComponent` — v12 M2 Task 2.7 Step 1.

The Component wraps :class:`PermissionDenialTracker` so denials emitted
by the upstream :class:`PermissionCheckComponent` are persisted on a
per-session tracker and exposed back to the Pipeline as
``denial_history`` state.

Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-pipeline-dag.md Task 2.7
"""
from __future__ import annotations



class TestDenialTrackingComponentImports:
    def test_module_imports(self) -> None:
        from llm_code.engine.components import denial_tracking as dt_mod

        assert hasattr(dt_mod, "DenialTrackingComponent")


class TestDenialTrackingComponentShape:
    def test_marked_as_component(self) -> None:
        from llm_code.engine.component import is_component
        from llm_code.engine.components.denial_tracking import (
            DenialTrackingComponent,
        )

        assert is_component(DenialTrackingComponent())

    def test_input_sockets(self) -> None:
        from llm_code.engine.component import get_input_sockets
        from llm_code.engine.components.denial_tracking import (
            DenialTrackingComponent,
        )

        inputs = get_input_sockets(DenialTrackingComponent)
        assert set(inputs) == {
            "allowed",
            "reason",
            "tool_name",
            "tool_use_id",
            "tool_args",
        }

    def test_output_sockets(self) -> None:
        from llm_code.engine.component import get_output_sockets
        from llm_code.engine.components.denial_tracking import (
            DenialTrackingComponent,
        )

        outputs = get_output_sockets(DenialTrackingComponent)
        assert set(outputs) == {"proceed", "denial_history"}

    def test_declares_state_writes(self) -> None:
        from llm_code.engine.components.denial_tracking import (
            DenialTrackingComponent,
        )

        writes = getattr(DenialTrackingComponent, "__state_writes__", frozenset())
        assert "denial_history" in writes


class TestDenialTrackingComponentRun:
    def test_allowed_call_records_nothing(self) -> None:
        from llm_code.engine.components.denial_tracking import (
            DenialTrackingComponent,
        )

        comp = DenialTrackingComponent()
        out = comp.run(
            allowed=True,
            reason="",
            tool_name="read_file",
            tool_use_id="t-1",
            tool_args={"path": "a"},
        )
        assert out["proceed"] is True
        assert out["denial_history"] == ()
        assert comp.tracker.count == 0

    def test_denied_call_recorded(self) -> None:
        from llm_code.engine.components.denial_tracking import (
            DenialTrackingComponent,
        )

        comp = DenialTrackingComponent()
        out = comp.run(
            allowed=False,
            reason="denied by deny_tools list",
            tool_name="bash",
            tool_use_id="t-42",
            tool_args={"cmd": "rm -rf /"},
        )
        assert out["proceed"] is False
        assert len(out["denial_history"]) == 1
        entry = out["denial_history"][0]
        assert entry.tool_name == "bash"
        assert entry.tool_use_id == "t-42"
        assert entry.reason == "denied by deny_tools list"

    def test_multiple_denials_accumulate(self) -> None:
        from llm_code.engine.components.denial_tracking import (
            DenialTrackingComponent,
        )

        comp = DenialTrackingComponent()
        comp.run(
            allowed=False,
            reason="r1",
            tool_name="t1",
            tool_use_id="u1",
            tool_args={},
        )
        comp.run(
            allowed=False,
            reason="r2",
            tool_name="t2",
            tool_use_id="u2",
            tool_args={},
        )
        last = comp.run(
            allowed=False,
            reason="r3",
            tool_name="t3",
            tool_use_id="u3",
            tool_args={},
        )
        assert len(last["denial_history"]) == 3
        assert [e.tool_name for e in last["denial_history"]] == ["t1", "t2", "t3"]

    def test_denial_history_is_tuple(self) -> None:
        """State-write values are immutable so downstream consumers cannot
        mutate the session's tracker state accidentally."""
        from llm_code.engine.components.denial_tracking import (
            DenialTrackingComponent,
        )

        comp = DenialTrackingComponent()
        out = comp.run(
            allowed=False,
            reason="r",
            tool_name="t",
            tool_use_id="u",
            tool_args={},
        )
        assert isinstance(out["denial_history"], tuple)

    def test_accepts_external_tracker(self) -> None:
        from llm_code.engine.components.denial_tracking import (
            DenialTrackingComponent,
        )
        from llm_code.runtime.permission_denial_tracker import (
            PermissionDenialTracker,
        )

        tracker = PermissionDenialTracker()
        comp = DenialTrackingComponent(tracker=tracker)
        comp.run(
            allowed=False,
            reason="r",
            tool_name="t",
            tool_use_id="u",
            tool_args={},
        )
        assert tracker.count == 1

    def test_proceed_matches_allowed(self) -> None:
        from llm_code.engine.components.denial_tracking import (
            DenialTrackingComponent,
        )

        comp = DenialTrackingComponent()
        assert comp.run(
            allowed=True,
            reason="",
            tool_name="t",
            tool_use_id="u",
            tool_args={},
        )["proceed"] is True
        assert comp.run(
            allowed=False,
            reason="nope",
            tool_name="t",
            tool_use_id="u",
            tool_args={},
        )["proceed"] is False

    def test_tool_args_defensive_copy(self) -> None:
        """Mutating the original args after record() must not change the
        stored denial entry."""
        from llm_code.engine.components.denial_tracking import (
            DenialTrackingComponent,
        )

        args = {"k": "v"}
        comp = DenialTrackingComponent()
        out = comp.run(
            allowed=False,
            reason="r",
            tool_name="t",
            tool_use_id="u",
            tool_args=args,
        )
        args["k"] = "MUTATED"
        assert out["denial_history"][0].input == {"k": "v"}

    def test_denial_source_policy(self) -> None:
        from llm_code.engine.components.denial_tracking import (
            DenialTrackingComponent,
        )
        from llm_code.runtime.permission_denial_tracker import DenialSource

        comp = DenialTrackingComponent()
        out = comp.run(
            allowed=False,
            reason="r",
            tool_name="t",
            tool_use_id="u",
            tool_args={},
        )
        assert out["denial_history"][0].source is DenialSource.POLICY

    def test_output_includes_all_entries_on_denial(self) -> None:
        from llm_code.engine.components.denial_tracking import (
            DenialTrackingComponent,
        )

        comp = DenialTrackingComponent()
        comp.run(
            allowed=False, reason="r1", tool_name="a", tool_use_id="1",
            tool_args={},
        )
        out = comp.run(
            allowed=False, reason="r2", tool_name="b", tool_use_id="2",
            tool_args={},
        )
        # On denial we expose *all* historical entries, not just the latest.
        assert len(out["denial_history"]) == 2

    def test_allow_after_denial_still_exposes_history(self) -> None:
        """Subsequent allows should still surface the running
        ``denial_history`` so observability spans can attach it."""
        from llm_code.engine.components.denial_tracking import (
            DenialTrackingComponent,
        )

        comp = DenialTrackingComponent()
        comp.run(
            allowed=False, reason="r", tool_name="t", tool_use_id="1",
            tool_args={},
        )
        out = comp.run(
            allowed=True, reason="", tool_name="t", tool_use_id="2",
            tool_args={},
        )
        assert out["proceed"] is True
        # History still reflects the prior denial; allows don't wipe state.
        assert len(out["denial_history"]) == 1


class TestDenialTrackingInPipeline:
    def test_wires_after_permission_check(self) -> None:
        from llm_code.engine.components.denial_tracking import (
            DenialTrackingComponent,
        )
        from llm_code.engine.components.permission_check import (
            PermissionCheckComponent,
        )
        from llm_code.engine.pipeline import Pipeline
        from llm_code.runtime.permissions import PermissionMode, PermissionPolicy

        p = Pipeline()
        p.add_component(
            "perm",
            PermissionCheckComponent(
                PermissionPolicy(mode=PermissionMode.FULL_ACCESS),
            ),
        )
        p.add_component("denial", DenialTrackingComponent())
        p.connect("perm.allowed", "denial.allowed")
        p.connect("perm.reason", "denial.reason")
        # Remaining denial inputs are entry-sockets fed by the caller.
        entry = p.inputs()
        assert "tool_name" in entry["denial"]
        assert "tool_use_id" in entry["denial"]
        assert "tool_args" in entry["denial"]

    def test_pipeline_run_records_denial(self) -> None:
        from llm_code.engine.components.denial_tracking import (
            DenialTrackingComponent,
        )
        from llm_code.engine.components.permission_check import (
            PermissionCheckComponent,
        )
        from llm_code.engine.pipeline import Pipeline
        from llm_code.runtime.permissions import PermissionMode, PermissionPolicy

        p = Pipeline()
        p.add_component(
            "perm",
            PermissionCheckComponent(
                PermissionPolicy(
                    mode=PermissionMode.FULL_ACCESS,
                    deny_tools=frozenset({"bash"}),
                ),
            ),
        )
        denial = DenialTrackingComponent()
        p.add_component("denial", denial)
        p.connect("perm.allowed", "denial.allowed")
        p.connect("perm.reason", "denial.reason")
        p.run({
            "perm": {
                "tool_name": "bash",
                "tool_args": {"cmd": "x"},
                "is_read_only": False,
            },
            "denial": {
                "tool_name": "bash",
                "tool_use_id": "t-1",
                "tool_args": {"cmd": "x"},
            },
        })
        assert denial.tracker.count == 1
