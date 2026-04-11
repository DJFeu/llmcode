"""Tests for the truncation registry (M15 Task C6)."""
from __future__ import annotations

from rich.console import Console

from llm_code.view.repl.components.truncation import TruncationRegistry


def _console() -> Console:
    return Console(width=80, record=True, color_system="truecolor")


def test_register_assigns_monotonic_ids() -> None:
    r = TruncationRegistry()
    a = r.register("tool_output", "a\nb")
    b = r.register("thinking", "c\nd")
    assert a.block_id == 1
    assert b.block_id == 2


def test_register_short_body_has_no_marker() -> None:
    r = TruncationRegistry()
    block = r.register("tool_output", "short", preview_lines=10)
    assert block.marker_text == ""


def test_register_long_body_marker_includes_line_count() -> None:
    r = TruncationRegistry()
    body = "\n".join(str(i) for i in range(200))
    block = r.register("tool_output", body, preview_lines=10)
    assert "190 more lines" in block.marker_text
    assert "Ctrl+O" in block.marker_text


def test_count_truncated_reflects_collapsed_blocks() -> None:
    r = TruncationRegistry()
    r.register("tool_output", "short")  # no marker, not counted
    r.register("thinking", "\n".join(str(i) for i in range(50)))
    assert r.count_truncated() == 1


def test_toggle_latest_expands_block() -> None:
    r = TruncationRegistry()
    body = "\n".join(str(i) for i in range(50))
    r.register("tool_output", body)
    console = _console()
    block = r.toggle_latest(console)
    assert block is not None
    assert block.current_state == "expanded"
    out = console.export_text()
    assert "expanded" in out
    assert "49" in out


def test_second_toggle_collapses_back() -> None:
    r = TruncationRegistry()
    body = "\n".join(str(i) for i in range(50))
    r.register("tool_output", body)
    console = _console()
    r.toggle_latest(console)
    r.toggle_latest(console)
    out = console.export_text()
    assert "re-collapsed" in out


def test_no_truncated_blocks_returns_none() -> None:
    r = TruncationRegistry()
    console = _console()
    assert r.toggle_latest(console) is None


def test_multiple_blocks_toggle_most_recent_first() -> None:
    r = TruncationRegistry()
    r.register("tool_output", "\n".join(str(i) for i in range(50)))
    second = r.register("diff", "\n".join(str(i) for i in range(100)))
    console = _console()
    toggled = r.toggle_latest(console)
    assert toggled is second
