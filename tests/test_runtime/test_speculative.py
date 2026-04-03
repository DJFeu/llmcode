"""Tests for Speculative Execution with Copy-on-Write overlay."""
from __future__ import annotations

import pathlib

import pytest

from llm_code.runtime.overlay import OverlayFS
from llm_code.runtime.speculative import SpeculativeExecutor
from llm_code.tools.write_file import WriteFileTool
from llm_code.tools.edit_file import EditFileTool


# ---------------------------------------------------------------------------
# OverlayFS tests
# ---------------------------------------------------------------------------

class TestOverlayFS:
    def test_init_creates_tmpdir(self, tmp_path):
        overlay = OverlayFS(base_dir=tmp_path, session_id="test-session")
        assert overlay.overlay_dir.exists()
        overlay.discard()

    def test_write_goes_to_overlay_not_real_fs(self, tmp_path):
        real_file = tmp_path / "hello.txt"
        overlay = OverlayFS(base_dir=tmp_path, session_id="test-write")
        overlay.write(real_file, "hello overlay")
        # Real file should NOT exist
        assert not real_file.exists()
        # Overlay mirror should exist
        assert overlay.overlay_dir.exists()
        overlay.discard()

    def test_read_from_overlay_when_present(self, tmp_path):
        real_file = tmp_path / "data.txt"
        overlay = OverlayFS(base_dir=tmp_path, session_id="test-read-overlay")
        overlay.write(real_file, "overlay content")
        result = overlay.read(real_file)
        assert result == "overlay content"
        overlay.discard()

    def test_read_fallback_to_real_fs(self, tmp_path):
        real_file = tmp_path / "existing.txt"
        real_file.write_text("real content")
        overlay = OverlayFS(base_dir=tmp_path, session_id="test-read-fallback")
        result = overlay.read(real_file)
        assert result == "real content"
        overlay.discard()

    def test_read_overlay_takes_precedence_over_real(self, tmp_path):
        real_file = tmp_path / "shared.txt"
        real_file.write_text("real content")
        overlay = OverlayFS(base_dir=tmp_path, session_id="test-precedence")
        overlay.write(real_file, "overlay content")
        result = overlay.read(real_file)
        assert result == "overlay content"
        overlay.discard()

    def test_read_raises_when_file_not_found(self, tmp_path):
        overlay = OverlayFS(base_dir=tmp_path, session_id="test-notfound")
        missing = tmp_path / "nonexistent.txt"
        with pytest.raises(FileNotFoundError):
            overlay.read(missing)
        overlay.discard()

    def test_commit_copies_to_real_fs(self, tmp_path):
        real_file = tmp_path / "output.txt"
        overlay = OverlayFS(base_dir=tmp_path, session_id="test-commit")
        overlay.write(real_file, "committed content")
        overlay.commit()
        assert real_file.exists()
        assert real_file.read_text() == "committed content"
        overlay.discard()

    def test_commit_creates_parent_dirs(self, tmp_path):
        deep_file = tmp_path / "deep" / "nested" / "file.txt"
        overlay = OverlayFS(base_dir=tmp_path, session_id="test-commit-dirs")
        overlay.write(deep_file, "nested content")
        overlay.commit()
        assert deep_file.exists()
        assert deep_file.read_text() == "nested content"
        overlay.discard()

    def test_discard_removes_tmpdir(self, tmp_path):
        overlay = OverlayFS(base_dir=tmp_path, session_id="test-discard")
        overlay.write(tmp_path / "x.txt", "content")
        overlay_dir = overlay.overlay_dir
        overlay.discard()
        assert not overlay_dir.exists()

    def test_list_pending_returns_written_paths(self, tmp_path):
        overlay = OverlayFS(base_dir=tmp_path, session_id="test-list")
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        overlay.write(f1, "aaa")
        overlay.write(f2, "bbb")
        pending = overlay.list_pending()
        assert f1 in pending
        assert f2 in pending
        overlay.discard()

    def test_list_pending_empty_initially(self, tmp_path):
        overlay = OverlayFS(base_dir=tmp_path, session_id="test-list-empty")
        pending = overlay.list_pending()
        assert pending == []
        overlay.discard()

    def test_context_manager_discards_on_exception(self, tmp_path):
        overlay_dir = None
        try:
            with OverlayFS(base_dir=tmp_path, session_id="test-ctx-exception") as overlay:
                overlay.write(tmp_path / "x.txt", "content")
                overlay_dir = overlay.overlay_dir
                raise ValueError("simulated error")
        except ValueError:
            pass
        assert overlay_dir is not None
        assert not overlay_dir.exists()

    def test_context_manager_keeps_overlay_on_normal_exit(self, tmp_path):
        """On normal exit, overlay is NOT auto-discarded; caller must commit/discard."""
        overlay_dir = None
        with OverlayFS(base_dir=tmp_path, session_id="test-ctx-normal") as overlay:
            overlay.write(tmp_path / "x.txt", "content")
            overlay_dir = overlay.overlay_dir
        # After normal __exit__, overlay_dir still exists (not auto-discarded)
        assert overlay_dir is not None
        assert overlay_dir.exists()
        overlay.discard()

    def test_write_relative_path_raises(self, tmp_path):
        overlay = OverlayFS(base_dir=tmp_path, session_id="test-rel")
        with pytest.raises(ValueError, match="absolute"):
            overlay.write(pathlib.Path("relative/path.txt"), "content")
        overlay.discard()


# ---------------------------------------------------------------------------
# WriteFileTool + overlay integration
# ---------------------------------------------------------------------------

class TestWriteFileToolWithOverlay:
    def test_write_with_overlay_goes_to_overlay(self, tmp_path):
        real_file = tmp_path / "output.txt"
        overlay = OverlayFS(base_dir=tmp_path, session_id="wf-overlay")
        tool = WriteFileTool()
        args = {"path": str(real_file), "content": "hello world\n"}
        result = tool.execute(args, overlay=overlay)
        assert not result.is_error
        assert not real_file.exists()
        assert overlay.read(real_file) == "hello world\n"
        overlay.discard()

    def test_write_without_overlay_writes_real_fs(self, tmp_path):
        real_file = tmp_path / "output.txt"
        tool = WriteFileTool()
        args = {"path": str(real_file), "content": "real write\n"}
        result = tool.execute(args)
        assert not result.is_error
        assert real_file.exists()
        assert real_file.read_text() == "real write\n"


# ---------------------------------------------------------------------------
# EditFileTool + overlay integration
# ---------------------------------------------------------------------------

class TestEditFileToolWithOverlay:
    def test_edit_with_overlay_reads_and_writes_overlay(self, tmp_path):
        real_file = tmp_path / "source.py"
        real_file.write_text("def hello():\n    pass\n")
        overlay = OverlayFS(base_dir=tmp_path, session_id="ef-overlay")
        tool = EditFileTool()
        args = {"path": str(real_file), "old": "pass", "new": "return 42"}
        result = tool.execute(args, overlay=overlay)
        assert not result.is_error
        # Real file unchanged
        assert real_file.read_text() == "def hello():\n    pass\n"
        # Overlay has edited content
        overlay_content = overlay.read(real_file)
        assert "return 42" in overlay_content
        assert "pass" not in overlay_content
        overlay.discard()

    def test_edit_with_overlay_file_not_in_overlay_or_real(self, tmp_path):
        overlay = OverlayFS(base_dir=tmp_path, session_id="ef-missing")
        tool = EditFileTool()
        missing = tmp_path / "nonexistent.py"
        args = {"path": str(missing), "old": "foo", "new": "bar"}
        result = tool.execute(args, overlay=overlay)
        assert result.is_error
        overlay.discard()

    def test_edit_with_overlay_uses_overlay_content_as_source(self, tmp_path):
        """When file was already written to overlay, edit reads from overlay."""
        real_file = tmp_path / "file.py"
        overlay = OverlayFS(base_dir=tmp_path, session_id="ef-chain")
        overlay.write(real_file, "x = 1\n")
        tool = EditFileTool()
        args = {"path": str(real_file), "old": "x = 1", "new": "x = 99"}
        result = tool.execute(args, overlay=overlay)
        assert not result.is_error
        overlay_content = overlay.read(real_file)
        assert "x = 99" in overlay_content
        overlay.discard()

    def test_edit_without_overlay_writes_real_fs(self, tmp_path):
        real_file = tmp_path / "script.py"
        real_file.write_text("a = 1\n")
        tool = EditFileTool()
        args = {"path": str(real_file), "old": "a = 1", "new": "a = 2"}
        result = tool.execute(args)
        assert not result.is_error
        assert real_file.read_text() == "a = 2\n"


# ---------------------------------------------------------------------------
# SpeculativeExecutor tests
# ---------------------------------------------------------------------------

class TestSpeculativeExecutor:
    def test_pre_execute_write_does_not_commit(self, tmp_path):
        real_file = tmp_path / "out.txt"
        tool = WriteFileTool()
        args = {"path": str(real_file), "content": "speculative\n"}
        executor = SpeculativeExecutor(tool=tool, args=args, base_dir=tmp_path, session_id="spec-write")
        result = executor.pre_execute()
        assert not result.is_error
        assert not real_file.exists()
        executor.deny()

    def test_confirm_commits_to_real_fs(self, tmp_path):
        real_file = tmp_path / "out.txt"
        tool = WriteFileTool()
        args = {"path": str(real_file), "content": "committed\n"}
        executor = SpeculativeExecutor(tool=tool, args=args, base_dir=tmp_path, session_id="spec-confirm")
        executor.pre_execute()
        executor.confirm()
        assert real_file.exists()
        assert real_file.read_text() == "committed\n"

    def test_deny_discards_overlay(self, tmp_path):
        real_file = tmp_path / "out.txt"
        tool = WriteFileTool()
        args = {"path": str(real_file), "content": "denied\n"}
        executor = SpeculativeExecutor(tool=tool, args=args, base_dir=tmp_path, session_id="spec-deny")
        executor.pre_execute()
        overlay_dir = executor.overlay.overlay_dir
        executor.deny()
        assert not real_file.exists()
        assert not overlay_dir.exists()

    def test_pre_execute_returns_tool_result(self, tmp_path):
        real_file = tmp_path / "result.txt"
        tool = WriteFileTool()
        args = {"path": str(real_file), "content": "test\n"}
        executor = SpeculativeExecutor(tool=tool, args=args, base_dir=tmp_path, session_id="spec-result")
        result = executor.pre_execute()
        from llm_code.tools.base import ToolResult
        assert isinstance(result, ToolResult)
        executor.deny()

    def test_pre_execute_idempotent_returns_cached(self, tmp_path):
        """Calling pre_execute twice returns the same cached result."""
        real_file = tmp_path / "cached.txt"
        tool = WriteFileTool()
        args = {"path": str(real_file), "content": "cached\n"}
        executor = SpeculativeExecutor(tool=tool, args=args, base_dir=tmp_path, session_id="spec-cached")
        r1 = executor.pre_execute()
        r2 = executor.pre_execute()
        assert r1 is r2
        executor.deny()

    def test_confirm_before_pre_execute_raises(self, tmp_path):
        tool = WriteFileTool()
        args = {"path": str(tmp_path / "x.txt"), "content": "x"}
        executor = SpeculativeExecutor(tool=tool, args=args, base_dir=tmp_path, session_id="spec-order")
        with pytest.raises(RuntimeError, match="pre_execute"):
            executor.confirm()

    def test_speculative_edit_confirm(self, tmp_path):
        real_file = tmp_path / "edit_target.py"
        real_file.write_text("value = 0\n")
        tool = EditFileTool()
        args = {"path": str(real_file), "old": "value = 0", "new": "value = 42"}
        executor = SpeculativeExecutor(tool=tool, args=args, base_dir=tmp_path, session_id="spec-edit")
        result = executor.pre_execute()
        assert not result.is_error
        # Real file still original
        assert real_file.read_text() == "value = 0\n"
        executor.confirm()
        # Now real file updated
        assert real_file.read_text() == "value = 42\n"

    def test_speculative_edit_deny(self, tmp_path):
        real_file = tmp_path / "edit_keep.py"
        real_file.write_text("original = True\n")
        tool = EditFileTool()
        args = {"path": str(real_file), "old": "original = True", "new": "original = False"}
        executor = SpeculativeExecutor(tool=tool, args=args, base_dir=tmp_path, session_id="spec-edit-deny")
        executor.pre_execute()
        executor.deny()
        # Real file unchanged
        assert real_file.read_text() == "original = True\n"

    def test_list_pending_changes(self, tmp_path):
        real_file = tmp_path / "pending.txt"
        tool = WriteFileTool()
        args = {"path": str(real_file), "content": "pending\n"}
        executor = SpeculativeExecutor(tool=tool, args=args, base_dir=tmp_path, session_id="spec-pending")
        executor.pre_execute()
        pending = executor.list_pending_changes()
        assert real_file in pending
        executor.deny()
