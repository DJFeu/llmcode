"""Tests for MultiEditTool — atomic multi-file search-and-replace."""
from llm_code.runtime.overlay import OverlayFS
from llm_code.tools.multi_edit import MultiEditTool


class TestMultiEditTool:
    def test_name(self):
        tool = MultiEditTool()
        assert tool.name == "multi_edit"

    def test_single_edit_success(self, tmp_path):
        f1 = tmp_path / "a.py"
        f1.write_text("hello world")
        tool = MultiEditTool()
        result = tool.execute({
            "edits": [{"path": str(f1), "old": "hello", "new": "goodbye"}]
        })
        assert not result.is_error
        assert f1.read_text() == "goodbye world"

    def test_multi_edit_atomic(self, tmp_path):
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("aaa")
        f2.write_text("bbb")
        tool = MultiEditTool()
        result = tool.execute({
            "edits": [
                {"path": str(f1), "old": "aaa", "new": "AAA"},
                {"path": str(f2), "old": "bbb", "new": "BBB"},
            ]
        })
        assert not result.is_error
        assert f1.read_text() == "AAA"
        assert f2.read_text() == "BBB"

    def test_rollback_on_second_edit_failure(self, tmp_path):
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("aaa")
        f2.write_text("bbb")
        tool = MultiEditTool()
        result = tool.execute({
            "edits": [
                {"path": str(f1), "old": "aaa", "new": "AAA"},
                {"path": str(f2), "old": "NOTFOUND", "new": "XXX"},
            ]
        })
        assert result.is_error
        # f1 should be rolled back
        assert f1.read_text() == "aaa"

    def test_validation_error_no_edits_applied(self, tmp_path):
        f1 = tmp_path / "a.py"
        f1.write_text("aaa")
        tool = MultiEditTool()
        result = tool.execute({
            "edits": [
                {"path": str(f1), "old": "aaa", "new": "AAA"},
                {"path": "/nonexistent/file.py", "old": "x", "new": "y"},
            ]
        })
        assert result.is_error
        # f1 untouched because validation failed before any apply
        assert f1.read_text() == "aaa"

    def test_max_edits_exceeded(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_text("x")
        tool = MultiEditTool()
        edits = [{"path": str(f), "old": "x", "new": "y"}] * 21
        result = tool.execute({"edits": edits})
        assert result.is_error
        assert "20" in result.output


class TestMultiEditOverlay:
    """Tests for MultiEditTool with OverlayFS support."""

    def test_overlay_edit_does_not_touch_real_fs(self, tmp_path):
        f1 = tmp_path / "a.py"
        f1.write_text("hello world")
        overlay = OverlayFS(base_dir=tmp_path, session_id="test-multi-overlay")
        tool = MultiEditTool()
        result = tool.execute(
            {"edits": [{"path": str(f1), "old": "hello", "new": "goodbye"}]},
            overlay=overlay,
        )
        assert not result.is_error
        # Real file unchanged
        assert f1.read_text() == "hello world"
        # Overlay has the edit
        assert overlay.read(f1) == "goodbye world"
        overlay.discard()

    def test_overlay_multi_edit_atomic(self, tmp_path):
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("aaa")
        f2.write_text("bbb")
        overlay = OverlayFS(base_dir=tmp_path, session_id="test-multi-atomic")
        tool = MultiEditTool()
        result = tool.execute(
            {"edits": [
                {"path": str(f1), "old": "aaa", "new": "AAA"},
                {"path": str(f2), "old": "bbb", "new": "BBB"},
            ]},
            overlay=overlay,
        )
        assert not result.is_error
        assert f1.read_text() == "aaa"  # real FS unchanged
        assert f2.read_text() == "bbb"  # real FS unchanged
        assert overlay.read(f1) == "AAA"
        assert overlay.read(f2) == "BBB"
        overlay.discard()

    def test_overlay_failure_no_overlay_writes(self, tmp_path):
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("aaa")
        f2.write_text("bbb")
        overlay = OverlayFS(base_dir=tmp_path, session_id="test-multi-fail")
        tool = MultiEditTool()
        result = tool.execute(
            {"edits": [
                {"path": str(f1), "old": "aaa", "new": "AAA"},
                {"path": str(f2), "old": "NOTFOUND", "new": "XXX"},
            ]},
            overlay=overlay,
        )
        assert result.is_error
        # Real FS unchanged
        assert f1.read_text() == "aaa"
        # Overlay should not have partial writes (no pending for f1)
        assert overlay.list_pending() == []
        overlay.discard()

    def test_overlay_reads_from_overlay_layer(self, tmp_path):
        """If a file was previously written to overlay, multi_edit sees that version."""
        f1 = tmp_path / "a.py"
        f1.write_text("original")
        overlay = OverlayFS(base_dir=tmp_path, session_id="test-multi-layer")
        # Pre-stage a different version in the overlay
        overlay.write(f1, "modified by prior tool")
        tool = MultiEditTool()
        result = tool.execute(
            {"edits": [{"path": str(f1), "old": "modified by prior tool", "new": "final version"}]},
            overlay=overlay,
        )
        assert not result.is_error
        assert overlay.read(f1) == "final version"
        assert f1.read_text() == "original"  # real FS untouched
        overlay.discard()
