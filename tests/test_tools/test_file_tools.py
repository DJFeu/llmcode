"""Tests for llm_code.tools.read_file, write_file, edit_file — TDD."""
from __future__ import annotations

import base64
import os
import pathlib

import pytest

from llm_code.tools.base import PermissionLevel
from llm_code.tools.read_file import ReadFileTool
from llm_code.tools.write_file import WriteFileTool
from llm_code.tools.edit_file import EditFileTool


# ---------------------------------------------------------------------------
# ReadFileTool
# ---------------------------------------------------------------------------

class TestReadFileTool:
    def test_name(self):
        assert ReadFileTool().name == "read_file"

    def test_permission(self):
        assert ReadFileTool().required_permission == PermissionLevel.READ_ONLY

    def test_reads_text_file_with_line_numbers(self, tmp_path):
        f = tmp_path / "hello.txt"
        f.write_text("line one\nline two\nline three\n")
        result = ReadFileTool().execute({"path": str(f)})
        assert result.is_error is False
        assert "1\tline one" in result.output
        assert "2\tline two" in result.output
        assert "3\tline three" in result.output

    def test_reads_with_offset(self, tmp_path):
        f = tmp_path / "hello.txt"
        f.write_text("line one\nline two\nline three\n")
        result = ReadFileTool().execute({"path": str(f), "offset": 2})
        assert "1\t" not in result.output
        assert "2\tline two" in result.output

    def test_reads_with_limit(self, tmp_path):
        f = tmp_path / "hello.txt"
        f.write_text("line one\nline two\nline three\n")
        result = ReadFileTool().execute({"path": str(f), "limit": 2})
        assert "1\tline one" in result.output
        assert "2\tline two" in result.output
        assert "3\tline three" not in result.output

    def test_missing_file_returns_error(self, tmp_path):
        result = ReadFileTool().execute({"path": str(tmp_path / "nope.txt")})
        assert result.is_error is True
        assert "nope.txt" in result.output

    def test_reads_image_returns_base64_metadata(self, tmp_path):
        img = tmp_path / "pixel.png"
        # Minimal 1x1 PNG bytes
        png_bytes = bytes([
            0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
        ])
        img.write_bytes(png_bytes)
        result = ReadFileTool().execute({"path": str(img)})
        assert result.is_error is False
        assert result.metadata is not None
        assert result.metadata["type"] == "image"
        assert result.metadata["media_type"] == "image/png"
        assert result.metadata["data"] == base64.b64encode(png_bytes).decode()

    def test_image_jpeg_media_type(self, tmp_path):
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff")
        result = ReadFileTool().execute({"path": str(img)})
        assert result.metadata["media_type"] == "image/jpeg"

    def test_image_webp_media_type(self, tmp_path):
        img = tmp_path / "anim.webp"
        img.write_bytes(b"RIFF")
        result = ReadFileTool().execute({"path": str(img)})
        assert result.metadata["media_type"] == "image/webp"

    def test_default_offset_is_1(self, tmp_path):
        f = tmp_path / "x.txt"
        f.write_text("a\nb\n")
        result = ReadFileTool().execute({"path": str(f)})
        assert "1\ta" in result.output

    def test_to_definition_has_schema(self):
        defn = ReadFileTool().to_definition()
        assert "path" in defn.input_schema.get("properties", {})


# ---------------------------------------------------------------------------
# WriteFileTool
# ---------------------------------------------------------------------------

class TestWriteFileTool:
    def test_name(self):
        assert WriteFileTool().name == "write_file"

    def test_permission(self):
        assert WriteFileTool().required_permission == PermissionLevel.WORKSPACE_WRITE

    def test_writes_file(self, tmp_path):
        dest = tmp_path / "out.txt"
        result = WriteFileTool().execute({"path": str(dest), "content": "hello\nworld\n"})
        assert result.is_error is False
        assert dest.read_text() == "hello\nworld\n"

    def test_returns_line_count(self, tmp_path):
        dest = tmp_path / "out.txt"
        result = WriteFileTool().execute({"path": str(dest), "content": "a\nb\nc\n"})
        assert "3" in result.output

    def test_auto_creates_parent_dirs(self, tmp_path):
        dest = tmp_path / "a" / "b" / "c.txt"
        result = WriteFileTool().execute({"path": str(dest), "content": "hi"})
        assert result.is_error is False
        assert dest.exists()

    def test_overwrites_existing_file(self, tmp_path):
        dest = tmp_path / "f.txt"
        dest.write_text("old content")
        WriteFileTool().execute({"path": str(dest), "content": "new content"})
        assert dest.read_text() == "new content"

    def test_to_definition_has_schema(self):
        defn = WriteFileTool().to_definition()
        props = defn.input_schema.get("properties", {})
        assert "path" in props
        assert "content" in props


# ---------------------------------------------------------------------------
# EditFileTool
# ---------------------------------------------------------------------------

class TestEditFileTool:
    def test_name(self):
        assert EditFileTool().name == "edit_file"

    def test_permission(self):
        assert EditFileTool().required_permission == PermissionLevel.WORKSPACE_WRITE

    def test_replaces_first_occurrence(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("foo bar foo")
        result = EditFileTool().execute({"path": str(f), "old": "foo", "new": "baz"})
        assert result.is_error is False
        assert f.read_text() == "baz bar foo"

    def test_replace_all(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("foo bar foo baz foo")
        result = EditFileTool().execute({"path": str(f), "old": "foo", "new": "X", "replace_all": True})
        assert result.is_error is False
        assert f.read_text() == "X bar X baz X"

    def test_returns_occurrence_info(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("abc abc abc")
        result = EditFileTool().execute({"path": str(f), "old": "abc", "new": "xyz", "replace_all": True})
        assert "3" in result.output

    def test_missing_file_returns_error(self, tmp_path):
        result = EditFileTool().execute({"path": str(tmp_path / "nope.py"), "old": "x", "new": "y"})
        assert result.is_error is True

    def test_old_not_found_returns_error(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("hello world")
        result = EditFileTool().execute({"path": str(f), "old": "NOTFOUND", "new": "x"})
        assert result.is_error is True
        assert "NOTFOUND" in result.output

    def test_replace_all_default_false(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("a a a")
        EditFileTool().execute({"path": str(f), "old": "a", "new": "b"})
        assert f.read_text() == "b a a"

    def test_to_definition_has_schema(self):
        defn = EditFileTool().to_definition()
        props = defn.input_schema.get("properties", {})
        assert "path" in props
        assert "old" in props
        assert "new" in props
