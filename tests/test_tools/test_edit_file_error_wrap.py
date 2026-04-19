"""Tests for LLMCodeError wiring into edit_file failures (S4.3).

edit_file attaches a structured LLMCodeError (as dict) to
ToolResult.metadata['llmcode_error'] on every failure path. Existing
callers that only read ``output`` / ``is_error`` stay unaffected; new
callers (SDK, /diagnose) can marshal a typed error.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from llm_code.error_model import ErrorSeverity, LLMCodeError
from llm_code.tools.edit_file import EditFileTool


@pytest.fixture
def tool() -> EditFileTool:
    return EditFileTool()


def _llmcode_err(result) -> dict | None:
    md = result.metadata or {}
    return md.get("llmcode_error")


class TestErrorWrapping:
    def test_file_not_found_has_structured_error(self, tmp_path: Path, tool: EditFileTool) -> None:
        missing = tmp_path / "nope.py"
        result = tool.execute({
            "path": str(missing),
            "old": "x",
            "new": "y",
        })
        assert result.is_error is True
        err = _llmcode_err(result)
        assert err is not None
        assert err["code"] == "E_FILE_NOT_FOUND"
        assert err["severity"] == "error"
        assert err["location"]["file_path"] == str(missing)

    def test_text_not_found_has_structured_error(self, tmp_path: Path, tool: EditFileTool) -> None:
        path = tmp_path / "a.py"
        path.write_text("hello\n")
        result = tool.execute({
            "path": str(path),
            "old": "UNICORN",
            "new": "UNICORN2",
        })
        assert result.is_error is True
        err = _llmcode_err(result)
        assert err is not None
        assert err["code"] == "E_PATCH_NO_MATCH"
        assert err["location"]["file_path"] == str(path)
        assert err["context"].get("old_preview") == "UNICORN"


class TestSuccessPathHasNoError:
    def test_metadata_does_not_carry_error_on_success(
        self, tmp_path: Path, tool: EditFileTool,
    ) -> None:
        path = tmp_path / "ok.py"
        path.write_text("hello\n")
        result = tool.execute({
            "path": str(path),
            "old": "hello",
            "new": "world",
        })
        assert result.is_error is False
        assert _llmcode_err(result) is None


class TestRemainingFailureSitesWrapped:
    """M1 — wire the remaining edit_file failure paths so every
    is_error=True result carries a structured ``llmcode_error``."""

    def test_protection_denied_has_structured_error(
        self, tmp_path: Path, tool: EditFileTool, monkeypatch,
    ) -> None:
        path = tmp_path / "secret.env"
        path.write_text("x=y\n")

        class _Denied:
            allowed = False
            reason = "writes to .env blocked by policy"
            severity = "block"

        monkeypatch.setattr(
            "llm_code.tools.edit_file.check_write",
            lambda _p: _Denied(),
        )
        result = tool.execute({"path": str(path), "old": "x=y", "new": "x=z"})
        assert result.is_error is True
        err = _llmcode_err(result)
        assert err is not None
        assert err["code"] == "E_FILE_WRITE_FORBIDDEN"
        assert err["location"]["file_path"] == str(path)
        assert "policy" in err["message"]

    def test_file_too_large_has_structured_error(
        self, tmp_path: Path, tool: EditFileTool, monkeypatch,
    ) -> None:
        path = tmp_path / "big.txt"
        path.write_text("tiny")
        real_stat = type(path).stat

        class _FakeStat:
            def __init__(self, size, mtime):
                self.st_size = size
                self.st_mtime = mtime

        def _stat(self, *args, **kwargs):  # noqa: ARG001
            r = real_stat(self)
            return _FakeStat(10_000_000_000, r.st_mtime)

        monkeypatch.setattr(type(path), "stat", _stat)
        result = tool.execute({"path": str(path), "old": "tiny", "new": "small"})
        assert result.is_error is True
        err = _llmcode_err(result)
        assert err is not None
        assert err["code"] == "E_FILE_TOO_LARGE"
        assert err["context"]["size_bytes"] == 10_000_000_000

    def test_read_permission_error_has_structured_error(
        self, tmp_path: Path, tool: EditFileTool, monkeypatch,
    ) -> None:
        path = tmp_path / "locked.py"
        path.write_text("x\n")

        def _boom(self, *args, **kwargs):  # noqa: ARG001
            raise PermissionError("Permission denied")

        monkeypatch.setattr(type(path), "read_text", _boom)
        result = tool.execute({"path": str(path), "old": "x", "new": "y"})
        assert result.is_error is True
        err = _llmcode_err(result)
        assert err is not None
        assert err["code"] == "E_FILE_READ_FAILED"

    def test_mtime_conflict_has_structured_error(
        self, tmp_path: Path, tool: EditFileTool, monkeypatch,
    ) -> None:
        import os

        path = tmp_path / "race.py"
        path.write_text("v1")
        first_mtime = path.stat().st_mtime
        # Force read_text to bump the on-disk mtime so the pre-write
        # conflict check sees "changed after we read it".
        original_read = type(path).read_text

        def racing_read(self, *args, **kwargs):
            content = original_read(self, *args, **kwargs)
            os.utime(str(self), (first_mtime + 10, first_mtime + 10))
            return content

        monkeypatch.setattr(type(path), "read_text", racing_read)

        result = tool.execute({"path": str(path), "old": "v1", "new": "v2"})
        assert result.is_error is True
        err = _llmcode_err(result)
        assert err is not None
        assert err["code"] == "E_FILE_MODIFIED_EXTERNALLY"
        assert err["location"]["file_path"] == str(path)


class TestLLMCodeErrorToolMetadataHelper:
    """The helper on LLMCodeError that builds a dict suitable for
    ToolResult.metadata. Separate test so the contract is discoverable
    without reading the tool wrap sites."""

    def test_to_tool_metadata_shape(self) -> None:
        err = LLMCodeError(
            code="E_TEST",
            message="bang",
            severity=ErrorSeverity.WARNING,
            context={"k": 1},
        )
        md = err.to_tool_metadata()
        assert isinstance(md, dict)
        assert "llmcode_error" in md
        payload = md["llmcode_error"]
        assert payload["code"] == "E_TEST"
        assert payload["severity"] == "warning"
        assert payload["context"] == {"k": 1}

    def test_to_tool_metadata_is_json_safe(self) -> None:
        import json
        err = LLMCodeError(
            code="E_X", message="m",
            context={"path": "/x", "line": 3},
        )
        md = err.to_tool_metadata()
        json.dumps(md)  # must not raise
