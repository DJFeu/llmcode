"""Tests for File + Search Tools v2 — safety classification, Pydantic validation, progress streaming."""
from __future__ import annotations

import pathlib
from typing import List

import pytest
from pydantic import ValidationError

from llm_code.tools.base import ToolProgress, ToolResult
from llm_code.tools.edit_file import EditFileTool, EditFileInput
from llm_code.tools.glob_search import GlobSearchTool, GlobSearchInput
from llm_code.tools.grep_search import GrepSearchTool, GrepSearchInput
from llm_code.tools.read_file import ReadFileTool, ReadFileInput
from llm_code.tools.write_file import WriteFileTool, WriteFileInput


# ---------------------------------------------------------------------------
# ReadFileTool v2
# ---------------------------------------------------------------------------


class TestReadFileInput:
    def test_valid(self) -> None:
        inp = ReadFileInput(path="/tmp/foo.txt")
        assert inp.path == "/tmp/foo.txt"
        assert inp.offset == 1
        assert inp.limit == 2000

    def test_custom_offset_limit(self) -> None:
        inp = ReadFileInput(path="/tmp/foo.txt", offset=10, limit=50)
        assert inp.offset == 10
        assert inp.limit == 50

    def test_missing_path_raises(self) -> None:
        with pytest.raises(ValidationError):
            ReadFileInput()  # type: ignore[call-arg]


class TestReadFileSafety:
    def setup_method(self) -> None:
        self.tool = ReadFileTool()

    def test_is_read_only_always_true(self) -> None:
        assert self.tool.is_read_only({"path": "/any/path"}) is True
        assert self.tool.is_read_only({}) is True

    def test_is_destructive_always_false(self) -> None:
        assert self.tool.is_destructive({"path": "/any/path"}) is False

    def test_is_concurrency_safe_always_true(self) -> None:
        assert self.tool.is_concurrency_safe({"path": "/any/path"}) is True

    def test_input_model_is_read_file_input(self) -> None:
        assert self.tool.input_model is ReadFileInput

    def test_validate_input_coerces(self) -> None:
        validated = self.tool.validate_input({"path": "/tmp/x.txt"})
        assert validated["path"] == "/tmp/x.txt"
        assert validated["offset"] == 1

    def test_validate_input_missing_path_raises(self) -> None:
        with pytest.raises(ValidationError):
            self.tool.validate_input({})


# ---------------------------------------------------------------------------
# WriteFileTool v2
# ---------------------------------------------------------------------------


class TestWriteFileInput:
    def test_valid(self) -> None:
        inp = WriteFileInput(path="/tmp/foo.txt", content="hello")
        assert inp.path == "/tmp/foo.txt"
        assert inp.content == "hello"

    def test_missing_path_raises(self) -> None:
        with pytest.raises(ValidationError):
            WriteFileInput(content="hello")  # type: ignore[call-arg]

    def test_missing_content_raises(self) -> None:
        with pytest.raises(ValidationError):
            WriteFileInput(path="/tmp/foo.txt")  # type: ignore[call-arg]


class TestWriteFileSafety:
    def setup_method(self) -> None:
        self.tool = WriteFileTool()

    def test_is_read_only_always_false(self) -> None:
        assert self.tool.is_read_only({"path": "/tmp/x"}) is False

    def test_is_destructive_always_false(self) -> None:
        assert self.tool.is_destructive({"path": "/tmp/x"}) is False

    def test_is_concurrency_safe_always_false(self) -> None:
        assert self.tool.is_concurrency_safe({"path": "/tmp/x"}) is False

    def test_input_model_is_write_file_input(self) -> None:
        assert self.tool.input_model is WriteFileInput

    def test_validate_input_coerces(self) -> None:
        validated = self.tool.validate_input({"path": "/tmp/x.txt", "content": "hi"})
        assert validated["path"] == "/tmp/x.txt"
        assert validated["content"] == "hi"

    def test_validate_input_missing_fields_raises(self) -> None:
        with pytest.raises(ValidationError):
            self.tool.validate_input({})


# ---------------------------------------------------------------------------
# EditFileTool v2
# ---------------------------------------------------------------------------


class TestEditFileInput:
    def test_valid(self) -> None:
        inp = EditFileInput(path="/tmp/foo.txt", old="old", new="new")
        assert inp.path == "/tmp/foo.txt"
        assert inp.old == "old"
        assert inp.new == "new"
        assert inp.replace_all is False

    def test_replace_all_flag(self) -> None:
        inp = EditFileInput(path="/tmp/foo.txt", old="x", new="y", replace_all=True)
        assert inp.replace_all is True

    def test_missing_path_raises(self) -> None:
        with pytest.raises(ValidationError):
            EditFileInput(old="x", new="y")  # type: ignore[call-arg]

    def test_missing_old_raises(self) -> None:
        with pytest.raises(ValidationError):
            EditFileInput(path="/tmp/f.txt", new="y")  # type: ignore[call-arg]

    def test_missing_new_raises(self) -> None:
        with pytest.raises(ValidationError):
            EditFileInput(path="/tmp/f.txt", old="x")  # type: ignore[call-arg]


class TestEditFileSafety:
    def setup_method(self) -> None:
        self.tool = EditFileTool()

    def test_is_read_only_always_false(self) -> None:
        assert self.tool.is_read_only({"path": "/tmp/x"}) is False

    def test_is_destructive_always_false(self) -> None:
        assert self.tool.is_destructive({"path": "/tmp/x"}) is False

    def test_is_concurrency_safe_always_false(self) -> None:
        assert self.tool.is_concurrency_safe({"path": "/tmp/x"}) is False

    def test_input_model_is_edit_file_input(self) -> None:
        assert self.tool.input_model is EditFileInput

    def test_validate_input_coerces(self) -> None:
        validated = self.tool.validate_input({"path": "/tmp/x.txt", "old": "a", "new": "b"})
        assert validated["replace_all"] is False

    def test_validate_input_missing_fields_raises(self) -> None:
        with pytest.raises(ValidationError):
            self.tool.validate_input({"path": "/tmp/x.txt"})


# ---------------------------------------------------------------------------
# GlobSearchTool v2
# ---------------------------------------------------------------------------


class TestGlobSearchInput:
    def test_valid(self) -> None:
        inp = GlobSearchInput(pattern="**/*.py")
        assert inp.pattern == "**/*.py"
        assert inp.path == "."

    def test_custom_path(self) -> None:
        inp = GlobSearchInput(pattern="*.txt", path="/tmp")
        assert inp.path == "/tmp"

    def test_missing_pattern_raises(self) -> None:
        with pytest.raises(ValidationError):
            GlobSearchInput()  # type: ignore[call-arg]


class TestGlobSearchSafety:
    def setup_method(self) -> None:
        self.tool = GlobSearchTool()

    def test_is_read_only_always_true(self) -> None:
        assert self.tool.is_read_only({"pattern": "**/*.py"}) is True
        assert self.tool.is_read_only({}) is True

    def test_is_destructive_always_false(self) -> None:
        assert self.tool.is_destructive({"pattern": "**/*.py"}) is False

    def test_is_concurrency_safe_always_true(self) -> None:
        assert self.tool.is_concurrency_safe({"pattern": "**/*.py"}) is True

    def test_input_model_is_glob_search_input(self) -> None:
        assert self.tool.input_model is GlobSearchInput

    def test_validate_input_coerces(self) -> None:
        validated = self.tool.validate_input({"pattern": "**/*.py"})
        assert validated["pattern"] == "**/*.py"
        assert validated["path"] == "."

    def test_validate_input_missing_pattern_raises(self) -> None:
        with pytest.raises(ValidationError):
            self.tool.validate_input({})


class TestGlobSearchProgress:
    def setup_method(self) -> None:
        self.tool = GlobSearchTool()

    def test_execute_with_progress_works(self, tmp_path: pathlib.Path) -> None:
        # Create some files
        for i in range(5):
            (tmp_path / f"file{i}.txt").write_text("content")

        events: List[ToolProgress] = []
        result = self.tool.execute_with_progress(
            {"pattern": "*.txt", "path": str(tmp_path)},
            on_progress=events.append,
        )
        assert isinstance(result, ToolResult)
        assert not result.is_error

    def test_execute_with_progress_emits_every_50(self, tmp_path: pathlib.Path) -> None:
        # Create 55 files to trigger at least one progress event
        for i in range(55):
            (tmp_path / f"file{i:03d}.py").write_text("x")

        events: List[ToolProgress] = []
        result = self.tool.execute_with_progress(
            {"pattern": "*.py", "path": str(tmp_path)},
            on_progress=events.append,
        )
        assert isinstance(result, ToolResult)
        assert len(events) >= 1
        for ev in events:
            assert isinstance(ev, ToolProgress)
            assert ev.tool_name == "glob_search"

    def test_progress_event_has_message(self, tmp_path: pathlib.Path) -> None:
        for i in range(55):
            (tmp_path / f"f{i}.py").write_text("x")

        events: List[ToolProgress] = []
        self.tool.execute_with_progress(
            {"pattern": "*.py", "path": str(tmp_path)},
            on_progress=events.append,
        )
        if events:
            assert events[0].message


# ---------------------------------------------------------------------------
# GrepSearchTool v2
# ---------------------------------------------------------------------------


class TestGrepSearchInput:
    def test_valid(self) -> None:
        inp = GrepSearchInput(pattern="foo")
        assert inp.pattern == "foo"
        assert inp.path == "."
        assert inp.glob == "**/*"
        assert inp.context == 0

    def test_custom_args(self) -> None:
        inp = GrepSearchInput(pattern="bar", path="/tmp", glob="*.py", context=2)
        assert inp.path == "/tmp"
        assert inp.glob == "*.py"
        assert inp.context == 2

    def test_missing_pattern_raises(self) -> None:
        with pytest.raises(ValidationError):
            GrepSearchInput()  # type: ignore[call-arg]


class TestGrepSearchSafety:
    def setup_method(self) -> None:
        self.tool = GrepSearchTool()

    def test_is_read_only_always_true(self) -> None:
        assert self.tool.is_read_only({"pattern": "foo"}) is True
        assert self.tool.is_read_only({}) is True

    def test_is_destructive_always_false(self) -> None:
        assert self.tool.is_destructive({"pattern": "foo"}) is False

    def test_is_concurrency_safe_always_true(self) -> None:
        assert self.tool.is_concurrency_safe({"pattern": "foo"}) is True

    def test_input_model_is_grep_search_input(self) -> None:
        assert self.tool.input_model is GrepSearchInput

    def test_validate_input_coerces(self) -> None:
        validated = self.tool.validate_input({"pattern": "foo"})
        assert validated["pattern"] == "foo"
        assert validated["context"] == 0

    def test_validate_input_missing_pattern_raises(self) -> None:
        with pytest.raises(ValidationError):
            self.tool.validate_input({})


class TestGrepSearchProgress:
    def setup_method(self) -> None:
        self.tool = GrepSearchTool()

    def test_execute_with_progress_works(self, tmp_path: pathlib.Path) -> None:
        for i in range(5):
            (tmp_path / f"file{i}.txt").write_text("hello world\nfoo bar\n")

        events: List[ToolProgress] = []
        result = self.tool.execute_with_progress(
            {"pattern": "hello", "path": str(tmp_path), "glob": "*.txt"},
            on_progress=events.append,
        )
        assert isinstance(result, ToolResult)
        assert not result.is_error

    def test_execute_with_progress_emits_every_100(self, tmp_path: pathlib.Path) -> None:
        # Create 105 files to cross the 100-file threshold
        for i in range(105):
            (tmp_path / f"file{i:04d}.txt").write_text("searchme content\n")

        events: List[ToolProgress] = []
        result = self.tool.execute_with_progress(
            {"pattern": "searchme", "path": str(tmp_path), "glob": "*.txt"},
            on_progress=events.append,
        )
        assert isinstance(result, ToolResult)
        assert len(events) >= 1
        for ev in events:
            assert isinstance(ev, ToolProgress)
            assert ev.tool_name == "grep_search"

    def test_progress_event_has_percent(self, tmp_path: pathlib.Path) -> None:
        for i in range(105):
            (tmp_path / f"f{i:04d}.txt").write_text("data\n")

        events: List[ToolProgress] = []
        self.tool.execute_with_progress(
            {"pattern": "data", "path": str(tmp_path), "glob": "*.txt"},
            on_progress=events.append,
        )
        # Events with percent should have valid float values
        for ev in events:
            if ev.percent is not None:
                assert 0.0 <= ev.percent <= 100.0
