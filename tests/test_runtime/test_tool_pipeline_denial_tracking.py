"""Tests for the denial-tracking helper wired into ToolExecutionPipeline
(follow-up of H11).

The pipeline has six deny branches:

    1. sub-agent role blocks the tool
    2. tool.classify() marks the args dangerous
    3. harness plan-mode refuses write tools
    4. permissions.authorize() returns DENY
    5. user rejects the interactive prompt (NEED_PROMPT → deny)
    6. pre_tool_use hook returns denied=True

Each branch should call the ``_record_denial`` helper so the denial
lands on ``runtime._permission_denial_tracker``. The helper itself is
the narrow contract we unit-test here — the full pipeline run is
exercised by the wider conversation test suite.
"""
from __future__ import annotations

from types import SimpleNamespace

from llm_code.runtime.permission_denial_tracker import (
    DenialSource,
    PermissionDenialTracker,
)
from llm_code.runtime.tool_pipeline import _record_denial


class TestRecordDenialHelper:
    def test_lazy_init_creates_tracker_on_runtime(self) -> None:
        rt = SimpleNamespace()
        assert not hasattr(rt, "_permission_denial_tracker")

        _record_denial(
            rt,
            tool_name="bash",
            tool_use_id="call_1",
            input={"command": "rm -rf /"},
            reason="dangerous pattern",
            source=DenialSource.POLICY,
        )

        tracker = rt._permission_denial_tracker
        assert isinstance(tracker, PermissionDenialTracker)
        assert tracker.count == 1
        assert tracker.entries()[0].tool_name == "bash"
        assert tracker.entries()[0].source is DenialSource.POLICY

    def test_reuses_existing_tracker(self) -> None:
        rt = SimpleNamespace(_permission_denial_tracker=PermissionDenialTracker())
        _record_denial(
            rt,
            tool_name="edit_file",
            tool_use_id="c1",
            input={"path": "/etc/passwd"},
            reason="outside workspace",
            source=DenialSource.HOOK,
        )
        _record_denial(
            rt,
            tool_name="bash",
            tool_use_id="c2",
            input={"command": "shutdown"},
            reason="user rejected",
            source=DenialSource.USER,
        )
        assert rt._permission_denial_tracker.count == 2
        sources = [e.source for e in rt._permission_denial_tracker.entries()]
        assert sources == [DenialSource.HOOK, DenialSource.USER]

    def test_missing_args_use_safe_defaults(self) -> None:
        """Some deny sites only know the tool name + reason — helper
        must still succeed with empty dict / empty tool_use_id."""
        rt = SimpleNamespace()
        _record_denial(
            rt,
            tool_name="bash",
            tool_use_id="",
            input={},
            reason="plan mode",
            source=DenialSource.POLICY,
        )
        entry = rt._permission_denial_tracker.entries()[0]
        assert entry.tool_use_id == ""
        assert entry.input == {}

    def test_helper_never_raises_even_when_record_fails(self) -> None:
        """Deny branches are already on the error path — the helper
        must not mask a genuine error by raising."""

        class BadTracker:
            def record(self, **kwargs):  # noqa: ANN003
                raise RuntimeError("tracker broken")

        rt = SimpleNamespace(_permission_denial_tracker=BadTracker())
        # Must not raise despite BadTracker.record throwing.
        _record_denial(
            rt,
            tool_name="bash",
            tool_use_id="c1",
            input={},
            reason="x",
            source=DenialSource.POLICY,
        )
