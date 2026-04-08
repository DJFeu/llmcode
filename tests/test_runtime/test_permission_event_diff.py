"""Tests for diff_lines / pending_files fields on StreamPermissionRequest."""
from __future__ import annotations

import dataclasses

import pytest

from llm_code.api.types import StreamPermissionRequest


@pytest.mark.unit
class TestStreamPermissionRequestDiffFields:
    def test_defaults_empty(self) -> None:
        ev = StreamPermissionRequest(tool_name="bash", args_preview="ls")
        assert ev.diff_lines == ()
        assert ev.pending_files == ()

    def test_fields_preserved(self) -> None:
        ev = StreamPermissionRequest(
            tool_name="edit_file",
            args_preview='{"path": "foo.py"}',
            diff_lines=("@@ -1 +1 @@", "-a", "+b"),
            pending_files=("foo.py",),
        )
        assert ev.diff_lines == ("@@ -1 +1 @@", "-a", "+b")
        assert ev.pending_files == ("foo.py",)

    def test_frozen(self) -> None:
        ev = StreamPermissionRequest(tool_name="bash", args_preview="ls")
        with pytest.raises(dataclasses.FrozenInstanceError):
            ev.tool_name = "other"  # type: ignore[misc]
