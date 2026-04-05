"""Tests for plan mode data structures."""
from __future__ import annotations

import pytest

from llm_code.runtime.plan import PlanEntry, PlanSummary, summarize_tool_call


class TestSummarizeToolCall:
    def test_edit_file_short_strings(self) -> None:
        result = summarize_tool_call(
            "edit_file",
            {"file_path": "foo.py", "old_string": "hello", "new_string": "world"},
        )
        assert result == "Edit foo.py: 'hello' -> 'world'"

    def test_edit_file_truncates_long_strings(self) -> None:
        old = "a" * 50
        new = "b" * 50
        result = summarize_tool_call(
            "edit_file",
            {"file_path": "bar.py", "old_string": old, "new_string": new},
        )
        assert "..." in result
        assert "bar.py" in result
        assert len(result) < 200

    def test_edit_file_missing_args(self) -> None:
        result = summarize_tool_call("edit_file", {})
        assert "?" in result

    def test_write_file(self) -> None:
        result = summarize_tool_call(
            "write_file",
            {"file_path": "new.py", "content": "x" * 100},
        )
        assert result == "Create new.py (100 chars)"

    def test_write_file_missing_args(self) -> None:
        result = summarize_tool_call("write_file", {})
        assert "?" in result
        assert "0 chars" in result

    def test_bash_short_command(self) -> None:
        result = summarize_tool_call("bash", {"command": "ls -la"})
        assert result == "Run: ls -la"

    def test_bash_truncates_long_command(self) -> None:
        cmd = "echo " + "x" * 100
        result = summarize_tool_call("bash", {"command": cmd})
        assert result.startswith("Run: ")
        assert "..." in result

    def test_bash_missing_args(self) -> None:
        result = summarize_tool_call("bash", {})
        assert "?" in result

    def test_generic_tool(self) -> None:
        result = summarize_tool_call("read_file", {"path": "/tmp/foo"})
        assert "read_file" in result
        assert "path" in result

    def test_generic_tool_no_args(self) -> None:
        result = summarize_tool_call("some_tool", {})
        assert "some_tool" in result


class TestPlanEntry:
    def test_frozen(self) -> None:
        entry = PlanEntry(tool_name="bash", args={"command": "ls"}, summary="Run: ls")
        with pytest.raises((AttributeError, TypeError)):
            entry.tool_name = "other"  # type: ignore[misc]

    def test_fields(self) -> None:
        entry = PlanEntry(
            tool_name="write_file",
            args={"file_path": "x.py", "content": ""},
            summary="Create x.py (0 chars)",
        )
        assert entry.tool_name == "write_file"
        assert entry.args == {"file_path": "x.py", "content": ""}
        assert entry.summary == "Create x.py (0 chars)"


class TestPlanSummary:
    def test_render_empty(self) -> None:
        ps = PlanSummary(entries=())
        assert ps.render() == "No operations in plan."

    def test_render_two_entries(self) -> None:
        entries = (
            PlanEntry(tool_name="bash", args={}, summary="Run: ls"),
            PlanEntry(tool_name="write_file", args={}, summary="Create foo.py (5 chars)"),
        )
        ps = PlanSummary(entries=entries)
        rendered = ps.render()
        assert "Plan (2 operations)" in rendered
        assert "1. [bash] Run: ls" in rendered
        assert "2. [write_file] Create foo.py (5 chars)" in rendered

    def test_render_single_entry(self) -> None:
        entries = (
            PlanEntry(tool_name="edit_file", args={}, summary="Edit a.py: 'x' -> 'y'"),
        )
        ps = PlanSummary(entries=entries)
        rendered = ps.render()
        assert "Plan (1 operations)" in rendered
        assert "1. [edit_file]" in rendered

    def test_frozen(self) -> None:
        ps = PlanSummary(entries=())
        with pytest.raises((AttributeError, TypeError)):
            ps.entries = ()  # type: ignore[misc]
