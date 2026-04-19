"""Tests for PermissionDenialTracker (H11).

A structured store for tool calls that were denied (by a hook, by the
permission policy, or by user choice) during a run. Makes the blocked
calls visible to:
    * enterprise logs / compliance reports
    * the ``/diagnose`` output
    * SDK callers that want to know what their policy rejected
"""
from __future__ import annotations

from llm_code.runtime.permission_denial_tracker import (
    DeniedToolCall,
    DenialSource,
    PermissionDenialTracker,
)


# ---------- DeniedToolCall dataclass ----------


class TestDeniedToolCall:
    def test_frozen(self) -> None:
        d = DeniedToolCall(
            tool_name="bash",
            tool_use_id="call_1",
            input={"command": "rm -rf /"},
            reason="dangerous pattern",
            source=DenialSource.HOOK,
        )
        # Structural fields preserved
        assert d.tool_name == "bash"
        assert d.tool_use_id == "call_1"
        assert d.reason == "dangerous pattern"
        assert d.source is DenialSource.HOOK
        # dataclass is frozen — mutation must raise
        try:
            d.tool_name = "other"  # type: ignore[misc]
        except Exception:
            return
        raise AssertionError("DeniedToolCall should be frozen")

    def test_auto_timestamp(self) -> None:
        d = DeniedToolCall(
            tool_name="bash", tool_use_id="call_1",
            input={}, reason="x", source=DenialSource.POLICY,
        )
        assert d.denied_at > 0


# ---------- Tracker ----------


class TestPermissionDenialTracker:
    def test_starts_empty(self) -> None:
        t = PermissionDenialTracker()
        assert t.entries() == ()
        assert t.count == 0

    def test_record_and_list(self) -> None:
        t = PermissionDenialTracker()
        t.record(
            tool_name="bash",
            tool_use_id="c1",
            input={"command": "rm"},
            reason="unsafe",
            source=DenialSource.POLICY,
        )
        t.record(
            tool_name="web_fetch",
            tool_use_id="c2",
            input={"url": "http://evil"},
            reason="domain blocked",
            source=DenialSource.HOOK,
        )
        entries = t.entries()
        assert len(entries) == 2
        assert entries[0].tool_name == "bash"
        assert entries[1].source is DenialSource.HOOK
        assert t.count == 2

    def test_entries_are_immutable_snapshot(self) -> None:
        t = PermissionDenialTracker()
        t.record(
            tool_name="bash", tool_use_id="c1",
            input={}, reason="x", source=DenialSource.POLICY,
        )
        snapshot = t.entries()
        # Mutating the snapshot must not affect future entries.
        assert isinstance(snapshot, tuple)
        t.record(
            tool_name="edit_file", tool_use_id="c2",
            input={}, reason="y", source=DenialSource.USER,
        )
        assert len(snapshot) == 1
        assert len(t.entries()) == 2

    def test_clear(self) -> None:
        t = PermissionDenialTracker()
        t.record(
            tool_name="bash", tool_use_id="c1",
            input={}, reason="x", source=DenialSource.POLICY,
        )
        t.clear()
        assert t.count == 0

    def test_as_report_empty(self) -> None:
        t = PermissionDenialTracker()
        report = t.as_report()
        assert report["total"] == 0
        assert report["by_tool"] == {}
        assert report["by_source"] == {}
        assert report["entries"] == []

    def test_as_report_aggregates(self) -> None:
        t = PermissionDenialTracker()
        t.record(
            tool_name="bash", tool_use_id="c1",
            input={"command": "x"}, reason="unsafe",
            source=DenialSource.POLICY,
        )
        t.record(
            tool_name="bash", tool_use_id="c2",
            input={"command": "y"}, reason="unsafe",
            source=DenialSource.POLICY,
        )
        t.record(
            tool_name="web_fetch", tool_use_id="c3",
            input={"url": "u"}, reason="blocked",
            source=DenialSource.HOOK,
        )
        report = t.as_report()
        assert report["total"] == 3
        assert report["by_tool"]["bash"] == 2
        assert report["by_tool"]["web_fetch"] == 1
        assert report["by_source"]["policy"] == 2
        assert report["by_source"]["hook"] == 1
        # Each entry serialised with the public fields
        assert {"tool_name", "tool_use_id", "reason", "source", "denied_at"} <= set(
            report["entries"][0].keys()
        )

    def test_filter_by_tool(self) -> None:
        t = PermissionDenialTracker()
        t.record(
            tool_name="bash", tool_use_id="c1",
            input={}, reason="x", source=DenialSource.POLICY,
        )
        t.record(
            tool_name="edit_file", tool_use_id="c2",
            input={}, reason="y", source=DenialSource.POLICY,
        )
        bash_only = t.filter_by_tool("bash")
        assert len(bash_only) == 1
        assert bash_only[0].tool_name == "bash"

    def test_recent_window(self) -> None:
        """recent(n) returns the last n entries in insertion order."""
        t = PermissionDenialTracker()
        for i in range(5):
            t.record(
                tool_name=f"t{i}", tool_use_id=f"c{i}",
                input={}, reason="x", source=DenialSource.POLICY,
            )
        last2 = t.recent(2)
        assert [e.tool_name for e in last2] == ["t3", "t4"]


# ---------- H6 deep wire: DeniedToolCall -> LLMCodeError ----------


class TestDeniedToolCallAsError:
    """Each DeniedToolCall can produce a structured LLMCodeError so
    downstream audit / SDK callers get a uniform type to marshal."""

    def _build(self, source: DenialSource) -> "DeniedToolCall":
        return DeniedToolCall(
            tool_name="bash",
            tool_use_id="call_7",
            input={"command": "rm -rf /"},
            reason="dangerous command",
            source=source,
        )

    def test_policy_maps_to_permission_denied_code(self) -> None:
        from llm_code.error_model import ErrorSeverity, LLMCodeError

        err = self._build(DenialSource.POLICY).as_error()
        assert isinstance(err, LLMCodeError)
        assert err.code == "E_PERMISSION_DENIED"
        assert err.severity is ErrorSeverity.WARNING

    def test_hook_maps_to_hook_denied_code(self) -> None:
        err = self._build(DenialSource.HOOK).as_error()
        assert err.code == "E_HOOK_DENIED"

    def test_user_maps_to_user_denied_code(self) -> None:
        err = self._build(DenialSource.USER).as_error()
        assert err.code == "E_USER_DENIED"

    def test_sandbox_maps_to_sandbox_denied_code(self) -> None:
        err = self._build(DenialSource.SANDBOX).as_error()
        assert err.code == "E_SANDBOX_DENIED"

    def test_other_maps_to_tool_denied_code(self) -> None:
        err = self._build(DenialSource.OTHER).as_error()
        assert err.code == "E_TOOL_DENIED"

    def test_message_is_the_reason(self) -> None:
        err = self._build(DenialSource.POLICY).as_error()
        assert err.message == "dangerous command"

    def test_context_carries_tool_details(self) -> None:
        err = self._build(DenialSource.POLICY).as_error()
        assert err.context["tool_name"] == "bash"
        assert err.context["tool_use_id"] == "call_7"
        assert err.context["source"] == "policy"
        # Input is passed through so audit can replay what was blocked.
        assert err.context["input"]["command"] == "rm -rf /"


class TestAsReportEmbedsErrors:
    """as_report() must include the per-entry LLMCodeError dict so
    enterprise logs can consume a single JSON payload."""

    def test_each_entry_has_error_field(self) -> None:
        t = PermissionDenialTracker()
        t.record(
            tool_name="bash", tool_use_id="c1",
            input={"command": "x"}, reason="unsafe",
            source=DenialSource.POLICY,
        )
        report = t.as_report()
        assert "error" in report["entries"][0]
        err = report["entries"][0]["error"]
        assert err["code"] == "E_PERMISSION_DENIED"
        assert err["severity"] == "warning"
        assert err["context"]["tool_name"] == "bash"
