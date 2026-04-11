"""Tests for ToolEventRegion (Style R)."""
from __future__ import annotations

import io
import time

import pytest
from rich.console import Console

from llm_code.view.repl.components.tool_event_renderer import (
    AUTO_EXPAND_DIFF_TOOLS,
    ToolEventRegion,
    _format_args_summary,
)


def _make(
    tool_name: str = "read_file",
    args: dict | None = None,
) -> tuple[ToolEventRegion, io.StringIO]:
    if args is None:
        args = {}
    capture = io.StringIO()
    console = Console(
        file=capture,
        force_terminal=True,
        color_system="truecolor",
        width=80,
    )
    region = ToolEventRegion(console=console, tool_name=tool_name, args=args)
    return region, capture


def _reset_capture(capture: io.StringIO) -> None:
    """Discard start-line output so commit-only assertions are isolated."""
    capture.truncate(0)
    capture.seek(0)


# === _format_args_summary helpers ===


def test_args_summary_empty():
    assert _format_args_summary({}) == ""


def test_args_summary_priority_path():
    assert _format_args_summary({"path": "foo.py"}) == "foo.py"


def test_args_summary_priority_command():
    assert _format_args_summary({"command": "ls -la"}) == "ls -la"


def test_args_summary_long_value_truncated():
    long_path = "a" * 50
    result = _format_args_summary({"path": long_path})
    assert result.endswith("...")
    assert len(result) <= 60


def test_args_summary_secondary_key_value():
    result = _format_args_summary({"url": "http://x", "method": "GET"})
    assert "http://x" in result
    assert "method=GET" in result


def test_args_summary_total_length_capped():
    result = _format_args_summary({
        "path": "foo",
        "extra": "bar" * 30,
        "more": "baz" * 30,
    })
    assert len(result) <= 60
    assert result.endswith("...")


def test_args_summary_non_priority_only():
    """When no priority field is present, k=v entries still render."""
    result = _format_args_summary({"timeout": 30, "retries": 3})
    assert "timeout=30" in result
    assert "retries=3" in result


# === Constructor prints start line ===


def test_start_line_printed_on_init():
    _, capture = _make(tool_name="read_file", args={"path": "foo.py"})
    out = capture.getvalue()
    assert "read_file" in out
    assert "foo.py" in out
    assert "▶" in out


def test_start_line_no_args():
    _, capture = _make(tool_name="list_files", args={})
    out = capture.getvalue()
    assert "list_files" in out
    assert "▶" in out


def test_region_starts_active():
    region, _ = _make()
    assert region.is_active is True


# === feed_stdout / stderr / diff ===


def test_feed_stdout_accumulates():
    region, _ = _make()
    region.feed_stdout("line 1")
    region.feed_stdout("line 2")
    assert region._stdout == ["line 1", "line 2"]


def test_feed_stderr_accumulates():
    region, _ = _make()
    region.feed_stderr("err 1")
    region.feed_stderr("err 2")
    assert region._stderr == ["err 1", "err 2"]


def test_feed_diff_stores():
    region, _ = _make()
    region.feed_diff("@@ -1,3 +1,5 @@\n-old\n+new")
    assert "@@" in region._diff_text


def test_feed_stdout_after_commit_is_noop():
    region, _ = _make()
    region.commit_success(summary="x")
    region.feed_stdout("ignored")
    assert region._stdout == []


def test_feed_stderr_after_commit_is_noop():
    region, _ = _make()
    region.commit_success(summary="x")
    region.feed_stderr("ignored")
    assert region._stderr == []


def test_feed_diff_after_commit_is_noop():
    region, _ = _make()
    region.commit_success(summary="x")
    region.feed_diff("@@ diff @@")
    assert region._diff_text == ""


# === commit_success ===


def test_commit_success_prints_check_mark():
    region, capture = _make(tool_name="read_file")
    _reset_capture(capture)
    region.commit_success(summary="47 lines")
    out = capture.getvalue()
    assert "✓" in out
    assert "read_file" in out
    # Rich numeric highlight splits "47 lines" across ANSI codes.
    assert "47" in out
    assert "lines" in out


def test_commit_success_default_summary_with_stdout():
    region, capture = _make()
    region.feed_stdout("line 1")
    region.feed_stdout("line 2")
    _reset_capture(capture)
    region.commit_success()
    out = capture.getvalue()
    assert "2" in out
    assert "lines" in out


def test_commit_success_default_summary_without_stdout():
    region, capture = _make()
    _reset_capture(capture)
    region.commit_success()
    out = capture.getvalue()
    assert "done" in out


def test_commit_success_is_idempotent():
    region, _ = _make()
    region.commit_success(summary="first")
    region.commit_success(summary="second")
    assert region._summary == "first"


def test_commit_success_marks_inactive():
    region, _ = _make()
    region.commit_success(summary="done")
    assert region.is_active is False


# === commit_failure ===


def test_commit_failure_prints_cross():
    region, capture = _make(tool_name="bash")
    _reset_capture(capture)
    region.commit_failure(error="nonzero", exit_code=1)
    out = capture.getvalue()
    assert "✗" in out
    assert "bash" in out
    assert "nonzero" in out
    assert "exit" in out
    assert "1" in out


def test_commit_failure_no_exit_code():
    region, capture = _make()
    _reset_capture(capture)
    region.commit_failure(error="boom")
    out = capture.getvalue()
    assert "boom" in out
    # Without an exit code, the summary line has no "exit N" segment
    # after the error.
    after_boom = out.split("boom")[1]
    assert "exit" not in after_boom


def test_commit_failure_auto_expands_stderr():
    region, capture = _make(tool_name="bash")
    region.feed_stderr("first stderr")
    region.feed_stderr("second stderr")
    region.feed_stderr("third stderr")
    _reset_capture(capture)
    region.commit_failure(error="crash")
    out = capture.getvalue()
    assert "first stderr" in out
    assert "second stderr" in out
    assert "third stderr" in out


def test_commit_failure_stderr_tail_limit():
    region, capture = _make(tool_name="bash")
    for i in range(20):
        region.feed_stderr(f"stderr_line_{i}")
    _reset_capture(capture)
    region.commit_failure(error="fail")
    out = capture.getvalue()
    # MAX_STDERR_TAIL_LINES = 12; 20 - 12 = 8 dropped from the head.
    assert "stderr_line_0" not in out
    assert "stderr_line_7" not in out
    # Lines 8..19 should appear.
    assert "stderr_line_8" in out
    assert "stderr_line_19" in out


def test_commit_failure_is_idempotent():
    region, _ = _make()
    region.commit_failure(error="first")
    region.commit_failure(error="second")
    assert region._error == "first"


# === Diff auto-expand ===


@pytest.mark.parametrize("tool_name", sorted(AUTO_EXPAND_DIFF_TOOLS))
def test_diff_auto_expand_for_diff_tools(tool_name):
    region, capture = _make(tool_name=tool_name, args={"path": "bar.py"})
    region.feed_diff("@@ -1 +1 @@\n-old\n+new")
    _reset_capture(capture)
    region.commit_success(summary="done")
    out = capture.getvalue()
    # Diff panel should appear with the diff content plus the commit line
    assert "@@" in out or "old" in out
    assert tool_name in out


def test_diff_not_expanded_for_non_diff_tools():
    region, capture = _make(tool_name="read_file")
    region.feed_diff("@@ -1 +1 @@\n-old\n+new")
    _reset_capture(capture)
    region.commit_success(summary="done")
    out = capture.getvalue()
    # read_file is not in AUTO_EXPAND_DIFF_TOOLS, so the diff should
    # not be rendered as a panel.
    assert "@@" not in out


def test_diff_empty_not_expanded():
    region, capture = _make(tool_name="edit_file", args={"path": "x.py"})
    # feed_diff never called
    _reset_capture(capture)
    region.commit_success(summary="no changes")
    out = capture.getvalue()
    assert "@@" not in out


def test_diff_whitespace_only_not_expanded():
    region, capture = _make(tool_name="edit_file", args={"path": "x.py"})
    region.feed_diff("   \n   \n")
    _reset_capture(capture)
    region.commit_success(summary="no changes")
    out = capture.getvalue()
    # Whitespace-only diff is treated as empty.
    assert "@@" not in out


# === Elapsed time ===


def test_elapsed_time_is_positive():
    region, _ = _make()
    time.sleep(0.02)
    region.commit_success(summary="x")
    assert region.elapsed_seconds >= 0.02


def test_elapsed_time_printed_in_summary():
    region, capture = _make()
    _reset_capture(capture)
    region.commit_success(summary="done")
    out = capture.getvalue()
    assert "s" in out  # "0.0s" or similar


# === Properties ===


def test_tool_name_property():
    region, _ = _make(tool_name="bash")
    assert region.tool_name == "bash"


def test_args_property_is_copy():
    """Mutating the returned dict must not affect the stored args."""
    original = {"path": "x.py"}
    region, _ = _make(args=original)
    returned = region.args
    returned["path"] = "y.py"
    assert region.args["path"] == "x.py"


def test_is_active_transitions_on_success():
    region, _ = _make()
    assert region.is_active is True
    region.commit_success(summary="x")
    assert region.is_active is False


def test_is_active_transitions_on_failure():
    region, _ = _make()
    assert region.is_active is True
    region.commit_failure(error="oops")
    assert region.is_active is False
