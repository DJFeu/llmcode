"""Tests for MultiEditTool — atomic multi-file search-and-replace."""
import pytest
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
