"""Tests for StreamingToolCollector."""
from __future__ import annotations

import pytest

from llm_code.tools.parsing import ParsedToolCall
from llm_code.tools.registry import ToolRegistry
from llm_code.tools.read_file import ReadFileTool
from llm_code.tools.write_file import WriteFileTool
from llm_code.tools.bash import BashTool
from llm_code.runtime.streaming_executor import StreamingToolCollector


def _call(name: str, args: dict) -> ParsedToolCall:
    return ParsedToolCall(id="test-id", name=name, args=args, source="native")


@pytest.fixture()
def registry() -> ToolRegistry:
    r = ToolRegistry()
    r.register(ReadFileTool())
    r.register(WriteFileTool())
    r.register(BashTool())
    return r


@pytest.fixture()
def collector(registry: ToolRegistry) -> StreamingToolCollector:
    return StreamingToolCollector(registry)


# ---------------------------------------------------------------------------
# Individual tool routing
# ---------------------------------------------------------------------------

def test_read_file_returned_immediately(collector: StreamingToolCollector) -> None:
    call = _call("read_file", {"path": "/tmp/foo.txt"})
    result = collector.on_tool_complete(call)
    assert result is call
    assert not collector.has_pending()


def test_write_file_buffered(collector: StreamingToolCollector) -> None:
    call = _call("write_file", {"path": "/tmp/foo.txt", "content": "hi"})
    result = collector.on_tool_complete(call)
    assert result is None
    assert collector.has_pending()


def test_bash_generic_buffered(collector: StreamingToolCollector) -> None:
    """Generic bash commands are FULL_ACCESS and not read-only → buffered."""
    call = _call("bash", {"command": "rm -f /tmp/x"})
    result = collector.on_tool_complete(call)
    assert result is None
    assert collector.has_pending()


def test_bash_ls_returned_immediately(collector: StreamingToolCollector) -> None:
    """'ls' matches _READ_ONLY_PATTERNS in BashTool → returned immediately."""
    call = _call("bash", {"command": "ls /tmp"})
    result = collector.on_tool_complete(call)
    assert result is call
    assert not collector.has_pending()


# ---------------------------------------------------------------------------
# flush_pending
# ---------------------------------------------------------------------------

def test_flush_returns_all_pending_and_clears(collector: StreamingToolCollector) -> None:
    write1 = _call("write_file", {"path": "/tmp/a.txt", "content": "a"})
    write2 = _call("write_file", {"path": "/tmp/b.txt", "content": "b"})
    collector.on_tool_complete(write1)
    collector.on_tool_complete(write2)

    pending = collector.flush_pending()
    assert pending == [write1, write2]
    assert not collector.has_pending()
    assert collector.flush_pending() == []


# ---------------------------------------------------------------------------
# has_pending
# ---------------------------------------------------------------------------

def test_has_pending_false_initially(collector: StreamingToolCollector) -> None:
    assert not collector.has_pending()


def test_has_pending_true_after_write(collector: StreamingToolCollector) -> None:
    collector.on_tool_complete(_call("write_file", {"path": "/tmp/x", "content": ""}))
    assert collector.has_pending()


# ---------------------------------------------------------------------------
# Mixed: reads come back immediately, writes are buffered
# ---------------------------------------------------------------------------

def test_mixed_reads_and_writes(collector: StreamingToolCollector) -> None:
    read1 = _call("read_file", {"path": "/tmp/a.txt"})
    read2 = _call("read_file", {"path": "/tmp/b.txt"})
    write1 = _call("write_file", {"path": "/tmp/c.txt", "content": "c"})

    r1 = collector.on_tool_complete(read1)
    r2 = collector.on_tool_complete(read2)
    r3 = collector.on_tool_complete(write1)

    assert r1 is read1
    assert r2 is read2
    assert r3 is None

    pending = collector.flush_pending()
    assert pending == [write1]


# ---------------------------------------------------------------------------
# Unknown tool falls back to buffering (safe default)
# ---------------------------------------------------------------------------

def test_unknown_tool_buffered(collector: StreamingToolCollector) -> None:
    call = _call("unknown_tool", {"x": 1})
    result = collector.on_tool_complete(call)
    assert result is None
    assert collector.has_pending()
