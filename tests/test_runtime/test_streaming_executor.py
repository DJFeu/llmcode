"""Tests for StreamingToolCollector and StreamingToolExecutor."""
from __future__ import annotations

import json

import pytest

from llm_code.tools.parsing import ParsedToolCall
from llm_code.tools.registry import ToolRegistry
from llm_code.tools.read_file import ReadFileTool
from llm_code.tools.write_file import WriteFileTool
from llm_code.tools.bash import BashTool
from llm_code.runtime.streaming_executor import (
    StreamingToolCollector,
    StreamingToolExecutor,
    _attempt_partial_json_recovery,
)


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


# ===========================================================================
# StreamingToolExecutor tests
# ===========================================================================

@pytest.fixture()
def executor(registry: ToolRegistry) -> StreamingToolExecutor:
    return StreamingToolExecutor(registry)


# ---------------------------------------------------------------------------
# Partial JSON recovery
# ---------------------------------------------------------------------------

def test_partial_json_recovery_complete() -> None:
    assert _attempt_partial_json_recovery('{"path": "/tmp/foo"}') == {"path": "/tmp/foo"}


def test_partial_json_recovery_missing_brace() -> None:
    result = _attempt_partial_json_recovery('{"path": "/tmp/foo"')
    assert result == {"path": "/tmp/foo"}


def test_partial_json_recovery_truncated_string() -> None:
    # Truncated mid-value — should fall back to empty dict gracefully
    result = _attempt_partial_json_recovery('{"path": "/tmp/fo')
    assert isinstance(result, dict)


def test_partial_json_recovery_empty() -> None:
    assert _attempt_partial_json_recovery("") == {}


def test_partial_json_recovery_whitespace() -> None:
    assert _attempt_partial_json_recovery("   ") == {}


# ---------------------------------------------------------------------------
# StreamingToolExecutor: read-only tool starts immediately
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_read_only_tool_starts_background_task(
    tmp_path,
    registry: ToolRegistry,
) -> None:
    """read_file is read-only → finalize() should create a background task."""
    # Write a temp file to read
    test_file = tmp_path / "hello.txt"
    test_file.write_text("hello world")

    executor = StreamingToolExecutor(registry)
    tool_id = "tool-read-1"
    executor.start_tool(tool_id, "read_file")
    executor.submit(tool_id, json.dumps({"path": str(test_file)}))
    executor.finalize(tool_id)

    # Task should have been created
    assert tool_id in executor._read_tasks

    # Collect results — should get the file content
    read_results, write_calls = await executor.collect_results()
    assert write_calls == []
    assert len(read_results) == 1
    assert "hello world" in read_results[0].content
    assert not read_results[0].is_error


# ---------------------------------------------------------------------------
# StreamingToolExecutor: write tool is queued, not executed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_write_tool_queued_not_executed(
    tmp_path,
    registry: ToolRegistry,
) -> None:
    """write_file is WRITE → finalize() should NOT create a background task."""
    executor = StreamingToolExecutor(registry)
    tool_id = "tool-write-1"
    executor.start_tool(tool_id, "write_file")
    executor.submit(tool_id, json.dumps({"path": str(tmp_path / "out.txt"), "content": "hi"}))
    executor.finalize(tool_id)

    assert tool_id not in executor._read_tasks
    assert executor.pending_write_count() == 1

    read_results, write_calls = await executor.collect_results()
    assert read_results == []
    assert len(write_calls) == 1
    assert write_calls[0].name == "write_file"


# ---------------------------------------------------------------------------
# StreamingToolExecutor: multiple concurrent reads
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multiple_concurrent_reads(
    tmp_path,
    registry: ToolRegistry,
) -> None:
    """Multiple read_file calls should all run concurrently."""
    files = []
    for i in range(3):
        f = tmp_path / f"file{i}.txt"
        f.write_text(f"content-{i}")
        files.append(f)

    executor = StreamingToolExecutor(registry)
    for i, f in enumerate(files):
        tid = f"read-{i}"
        executor.start_tool(tid, "read_file")
        executor.submit(tid, json.dumps({"path": str(f)}))
        executor.finalize(tid)

    assert len(executor._read_tasks) == 3

    read_results, write_calls = await executor.collect_results()
    assert len(read_results) == 3
    assert write_calls == []

    all_content = "\n".join(r.content for r in read_results)
    for i in range(3):
        assert f"content-{i}" in all_content


# ---------------------------------------------------------------------------
# StreamingToolExecutor: partial JSON accumulation via submit()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_submit_accumulates_chunks(
    tmp_path,
    registry: ToolRegistry,
) -> None:
    """JSON input delivered in chunks should be reassembled correctly."""
    test_file = tmp_path / "chunk.txt"
    test_file.write_text("chunked content")

    path_str = str(test_file)
    # Split the JSON into 3 chunks
    full_json = json.dumps({"path": path_str})
    chunk_size = len(full_json) // 3
    chunks = [
        full_json[:chunk_size],
        full_json[chunk_size:chunk_size * 2],
        full_json[chunk_size * 2:],
    ]

    executor = StreamingToolExecutor(registry)
    tool_id = "tool-chunked"
    executor.start_tool(tool_id, "read_file")
    for chunk in chunks:
        executor.submit(tool_id, chunk)
    executor.finalize(tool_id)

    read_results, _ = await executor.collect_results()
    assert len(read_results) == 1
    assert "chunked content" in read_results[0].content


# ---------------------------------------------------------------------------
# StreamingToolExecutor: mixed reads and writes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mixed_reads_and_writes_executor(
    tmp_path,
    registry: ToolRegistry,
) -> None:
    """Reads run concurrently; writes are returned as ParsedToolCall list."""
    read_file = tmp_path / "r.txt"
    read_file.write_text("read-data")

    executor = StreamingToolExecutor(registry)

    # Read tool
    executor.start_tool("rid", "read_file")
    executor.submit("rid", json.dumps({"path": str(read_file)}))
    executor.finalize("rid")

    # Write tool
    executor.start_tool("wid", "write_file")
    executor.submit("wid", json.dumps({"path": str(tmp_path / "w.txt"), "content": "data"}))
    executor.finalize("wid")

    read_results, write_calls = await executor.collect_results()

    assert len(read_results) == 1
    assert "read-data" in read_results[0].content
    assert len(write_calls) == 1
    assert write_calls[0].name == "write_file"


# ---------------------------------------------------------------------------
# StreamingToolExecutor: unknown tool is buffered (safe default)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_tool_is_buffered_by_executor(
    registry: ToolRegistry,
) -> None:
    executor = StreamingToolExecutor(registry)
    executor.start_tool("uid", "mystery_tool")
    executor.submit("uid", json.dumps({"x": 1}))
    executor.finalize("uid")

    assert executor.pending_write_count() == 1
    read_results, write_calls = await executor.collect_results()
    assert read_results == []
    assert len(write_calls) == 1


# ---------------------------------------------------------------------------
# StreamingToolExecutor: finalize without start_tool is a no-op
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_finalize_without_start_is_noop(registry: ToolRegistry) -> None:
    executor = StreamingToolExecutor(registry)
    # Should not raise
    executor.finalize("nonexistent-id")
    read_results, write_calls = await executor.collect_results()
    assert read_results == []
    assert write_calls == []
