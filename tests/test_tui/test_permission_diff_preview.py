"""Tests for diff preview surfaced in PermissionInline widget."""
from __future__ import annotations

import pytest

from llm_code.tui.chat_widgets import PermissionInline


@pytest.mark.unit
class TestPermissionInlineDiffPreview:
    def test_no_diff_data_renders_baseline(self) -> None:
        baseline = PermissionInline("bash", '{"command": "ls"}').render().plain
        assert "Files to modify" not in baseline
        assert "@@" not in baseline

    def test_pending_files_listed(self) -> None:
        w = PermissionInline(
            "edit_file",
            '{"path": "foo.py"}',
            pending_files=("foo.py", "bar.py"),
        )
        plain = w.render().plain
        assert "Files to modify:" in plain
        assert "foo.py" in plain
        assert "bar.py" in plain

    def test_diff_lines_rendered(self) -> None:
        diff = (
            "@@ -1,2 +1,2 @@",
            "-old",
            "+new",
            " ctx",
        )
        w = PermissionInline(
            "edit_file",
            '{"path": "foo.py"}',
            diff_lines=diff,
            pending_files=("foo.py",),
        )
        plain = w.render().plain
        assert "old" in plain
        assert "new" in plain

    def test_diff_truncates_at_20_lines(self) -> None:
        body = ["@@ -1,40 +1,40 @@"] + [f"+line{i}" for i in range(40)]
        w = PermissionInline(
            "write_file",
            '{"path": "x.py"}',
            diff_lines=tuple(body),
        )
        plain = w.render().plain
        # render_diff_lines emits a "more line(s)" footer when truncated
        assert "more line" in plain

    def test_bash_no_diff_section(self) -> None:
        w = PermissionInline("bash", '{"command": "ls"}')
        plain = w.render().plain
        assert "@@" not in plain
        assert "Files to modify" not in plain
