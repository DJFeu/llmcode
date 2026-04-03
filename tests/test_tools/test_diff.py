"""Tests for llm_code.utils.diff — TDD."""
from __future__ import annotations

from io import StringIO

from rich.console import Console

from llm_code.utils.diff import DiffHunk, generate_diff, count_changes
from llm_code.tools.edit_file import EditFileTool
from llm_code.tools.write_file import WriteFileTool
from llm_code.cli.render import TerminalRenderer
from llm_code.tools.base import ToolResult


class TestDiffHunkImmutability:
    def test_frozen(self):
        hunk = DiffHunk(
            old_start=1, old_lines=1, new_start=1, new_lines=1,
            lines=("+added",),
        )
        try:
            hunk.old_start = 99
            assert False, "Should raise"
        except AttributeError:
            pass

    def test_lines_is_tuple(self):
        hunk = DiffHunk(
            old_start=1, old_lines=1, new_start=1, new_lines=1,
            lines=("+added",),
        )
        assert isinstance(hunk.lines, tuple)


class TestGenerateDiff:
    def test_identical_returns_empty(self):
        hunks = generate_diff("hello\n", "hello\n", "test.py")
        assert hunks == []

    def test_single_line_change(self):
        old = "hello\n"
        new = "world\n"
        hunks = generate_diff(old, new, "test.py")
        assert len(hunks) == 1
        assert any(line.startswith("-") for line in hunks[0].lines)
        assert any(line.startswith("+") for line in hunks[0].lines)

    def test_addition(self):
        old = "line1\nline2\n"
        new = "line1\nline2\nline3\n"
        hunks = generate_diff(old, new, "file.py")
        assert len(hunks) >= 1
        added = [line for h in hunks for line in h.lines if line.startswith("+")]
        assert any("line3" in line for line in added)

    def test_deletion(self):
        old = "line1\nline2\nline3\n"
        new = "line1\nline3\n"
        hunks = generate_diff(old, new, "file.py")
        removed = [line for h in hunks for line in h.lines if line.startswith("-")]
        assert any("line2" in line for line in removed)

    def test_context_lines_default_3(self):
        lines = [f"line{i}\n" for i in range(20)]
        old = "".join(lines)
        modified = list(lines)
        modified[10] = "CHANGED\n"
        new = "".join(modified)
        hunks = generate_diff(old, new, "big.py")
        context = [line for h in hunks for line in h.lines if line.startswith(" ")]
        # At most 3 context lines before and 3 after the change
        assert len(context) <= 6

    def test_truncate_at_500_lines(self):
        old = "".join(f"line{i}\n" for i in range(600))
        new = "".join(f"LINE{i}\n" for i in range(600))
        hunks = generate_diff(old, new, "huge.py")
        total_lines = sum(len(h.lines) for h in hunks)
        assert total_lines <= 500

    def test_hunk_positions(self):
        old = "a\nb\nc\n"
        new = "a\nB\nc\n"
        hunks = generate_diff(old, new, "pos.py")
        assert hunks[0].old_start >= 1
        assert hunks[0].new_start >= 1
        assert hunks[0].old_lines >= 1
        assert hunks[0].new_lines >= 1

    def test_empty_old_full_new(self):
        hunks = generate_diff("", "new content\n", "new.py")
        assert len(hunks) >= 1
        added = [line for h in hunks for line in h.lines if line.startswith("+")]
        assert len(added) >= 1

    def test_to_dict(self):
        hunk = DiffHunk(
            old_start=1, old_lines=2, new_start=1, new_lines=3,
            lines=(" ctx", "-old", "+new", "+extra"),
        )
        d = hunk.to_dict()
        assert d["old_start"] == 1
        assert d["new_lines"] == 3
        assert d["lines"] == [" ctx", "-old", "+new", "+extra"]


class TestCountChanges:
    def test_counts_adds_and_dels(self):
        hunks = generate_diff("a\nb\n", "a\nB\nc\n", "f.py")
        adds, dels = count_changes(hunks)
        assert adds >= 1
        assert dels >= 1

    def test_no_changes(self):
        adds, dels = count_changes([])
        assert adds == 0
        assert dels == 0


class TestEditFileDiffMetadata:
    def test_result_contains_diff_metadata(self, tmp_path):
        f = tmp_path / "demo.py"
        f.write_text("hello world\n")
        result = EditFileTool().execute({
            "path": str(f),
            "old": "hello",
            "new": "goodbye",
        })
        assert result.metadata is not None
        assert "diff" in result.metadata
        assert len(result.metadata["diff"]) >= 1

    def test_diff_metadata_has_correct_shape(self, tmp_path):
        f = tmp_path / "demo.py"
        f.write_text("alpha\nbeta\n")
        result = EditFileTool().execute({
            "path": str(f),
            "old": "beta",
            "new": "gamma",
        })
        hunk = result.metadata["diff"][0]
        assert "old_start" in hunk
        assert "new_start" in hunk
        assert "lines" in hunk
        assert isinstance(hunk["lines"], list)

    def test_diff_metadata_adds_and_dels(self, tmp_path):
        f = tmp_path / "demo.py"
        f.write_text("a\nb\nc\n")
        result = EditFileTool().execute({
            "path": str(f),
            "old": "b",
            "new": "B\nB2",
        })
        assert result.metadata is not None
        assert result.metadata.get("additions", 0) >= 1
        assert result.metadata.get("deletions", 0) >= 1


class TestWriteFileDiffMetadata:
    def test_new_file_has_no_diff(self, tmp_path):
        f = tmp_path / "brand_new.py"
        result = WriteFileTool().execute({"path": str(f), "content": "hello\n"})
        assert result.metadata is None or "diff" not in (result.metadata or {})

    def test_overwrite_produces_diff(self, tmp_path):
        f = tmp_path / "existing.py"
        f.write_text("old content\n")
        result = WriteFileTool().execute({"path": str(f), "content": "new content\n"})
        assert result.metadata is not None
        assert "diff" in result.metadata
        assert len(result.metadata["diff"]) >= 1

    def test_overwrite_identical_no_diff(self, tmp_path):
        f = tmp_path / "same.py"
        f.write_text("same\n")
        result = WriteFileTool().execute({"path": str(f), "content": "same\n"})
        # No diff when content is identical
        assert result.metadata is None or len(result.metadata.get("diff", [])) == 0

    def test_overwrite_counts(self, tmp_path):
        f = tmp_path / "counts.py"
        f.write_text("a\nb\nc\n")
        result = WriteFileTool().execute({"path": str(f), "content": "a\nB\nc\nd\n"})
        assert result.metadata is not None
        assert result.metadata["additions"] >= 1
        assert result.metadata["deletions"] >= 1


class TestRichDiffRendering:
    def test_renders_diff_panel_with_colors(self):
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=100)
        renderer = TerminalRenderer(console=console)
        result = ToolResult(
            output="Replaced 1 occurrence(s) in test.py",
            metadata={
                "diff": [{
                    "old_start": 1, "old_lines": 1, "new_start": 1, "new_lines": 1,
                    "lines": ["-old line", "+new line"],
                }],
                "additions": 1,
                "deletions": 1,
            },
        )
        renderer.render_tool_panel("edit_file", {"path": "test.py"}, result)
        output = buf.getvalue()
        assert "old line" in output
        assert "new line" in output

    def test_no_diff_renders_normally(self):
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=100)
        renderer = TerminalRenderer(console=console)
        result = ToolResult(output="Wrote 10 lines to test.py")
        renderer.render_tool_panel("write_file", {"path": "test.py"}, result)
        output = buf.getvalue()
        assert "Wrote 10 lines" in output
