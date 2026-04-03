"""StreamingToolCollector and StreamingToolExecutor: route and execute tools during streaming."""
from __future__ import annotations

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from llm_code.api.types import ToolResultBlock
from llm_code.tools.parsing import ParsedToolCall
from llm_code.tools.registry import ToolRegistry

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Thread pool shared for background read-only tool execution during streaming
_STREAMING_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="streaming-tool")


def _attempt_partial_json_recovery(partial: str) -> dict:
    """Try to recover a valid dict from partial/malformed JSON.

    Attempts several repair strategies in order:
    1. Direct parse (already complete)
    2. Append ``}``
    3. Append ``"}``
    4. Append ``"}``  (for unclosed string + object)
    5. Return empty dict as fallback
    """
    candidates = [
        partial,
        partial + "}",
        partial + '"}',
        partial + '"}}',
        partial + "}}",
    ]
    for candidate in candidates:
        try:
            result = json.loads(candidate)
            if isinstance(result, dict):
                return result
        except (json.JSONDecodeError, ValueError):
            continue
    return {}


class StreamingToolExecutor:
    """Execute read-only tools concurrently while the model is still streaming.

    Usage pattern (mirrors the streaming loop in conversation.py):

    .. code-block:: python

        executor = StreamingToolExecutor(registry, permission_policy)

        # Inside streaming loop:
        if isinstance(event, StreamToolUseStart):
            executor.start_tool(event.id, event.name)
        elif isinstance(event, StreamToolUseInputDelta):
            executor.submit(event.id, event.partial_json)

        # After each tool input is complete (StreamToolUseStop or next tool start):
        executor.finalize(tool_use_id)   # triggers background execution for reads

        # After stream ends, collect all results:
        results = await executor.collect_results()

    The executor decides at :meth:`finalize` time whether a tool is read-only and
    concurrency-safe.  If yes, it starts a background ``asyncio.Task``.  Write
    tools are queued and returned unfired so conversation.py can execute them via
    the normal ``_execute_tool_with_streaming`` path.
    """

    def __init__(self, tool_registry: ToolRegistry, permission_policy: Any = None) -> None:
        self._registry = tool_registry
        self._permissions = permission_policy

        # json_parts accumulation: tool_use_id -> list[str]
        self._json_parts: dict[str, list[str]] = {}
        # tool names: tool_use_id -> name
        self._tool_names: dict[str, str] = {}

        # background tasks for read-only tools: tool_use_id -> Task
        self._read_tasks: dict[str, asyncio.Task] = {}
        # pending write calls (not yet executed)
        self._write_calls: list[ParsedToolCall] = []

    def start_tool(self, tool_use_id: str, name: str) -> None:
        """Register a new tool use beginning (StreamToolUseStart event)."""
        self._tool_names[tool_use_id] = name
        self._json_parts[tool_use_id] = []

    def submit(self, tool_use_id: str, partial_json: str) -> None:
        """Accumulate a partial JSON chunk (StreamToolUseInputDelta event)."""
        if tool_use_id in self._json_parts:
            self._json_parts[tool_use_id].append(partial_json)

    def finalize(self, tool_use_id: str) -> None:
        """Mark tool input as complete; launch background execution if read-only.

        For read-only + concurrency-safe tools: starts an asyncio.Task immediately.
        For write tools: queues a ParsedToolCall for later sequential execution.
        """
        name = self._tool_names.get(tool_use_id)
        if name is None:
            logger.debug("finalize called for unknown tool_use_id %s", tool_use_id)
            return

        raw_json = "".join(self._json_parts.get(tool_use_id, []))
        try:
            args = json.loads(raw_json) if raw_json.strip() else {}
        except (json.JSONDecodeError, ValueError):
            args = _attempt_partial_json_recovery(raw_json)

        tool = self._registry.get(name)
        if tool is not None and tool.is_read_only(args) and tool.is_concurrency_safe(args):
            # Start background execution immediately
            call = ParsedToolCall(id=tool_use_id, name=name, args=args, source="native")
            task = asyncio.get_event_loop().create_task(
                self._execute_read_tool(tool_use_id, call, tool, args),
                name=f"streaming-read-{name}-{tool_use_id[:8]}",
            )
            self._read_tasks[tool_use_id] = task
        else:
            # Queue for sequential execution after stream completes
            call = ParsedToolCall(id=tool_use_id, name=name, args=args, source="native")
            self._write_calls.append(call)

    async def _execute_read_tool(
        self,
        tool_use_id: str,
        call: ParsedToolCall,
        tool: Any,
        args: dict,
    ) -> ToolResultBlock:
        """Run the tool in a thread pool and return a ToolResultBlock."""
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                _STREAMING_EXECUTOR,
                lambda: tool.execute(args),
            )
            return ToolResultBlock(
                tool_use_id=tool_use_id,
                content=result.output,
                is_error=result.is_error,
            )
        except Exception as exc:
            logger.warning("Background read tool %s failed: %s", call.name, exc)
            return ToolResultBlock(
                tool_use_id=tool_use_id,
                content=f"Tool execution failed: {exc}",
                is_error=True,
            )

    async def collect_results(self) -> tuple[list[ToolResultBlock], list[ParsedToolCall]]:
        """Wait for all background read tasks; return (read_results, write_calls).

        - ``read_results``: ToolResultBlocks for all read-only tools that ran concurrently
        - ``write_calls``: ParsedToolCalls for write tools that still need execution
        """
        read_results: list[ToolResultBlock] = []
        if self._read_tasks:
            done = await asyncio.gather(*self._read_tasks.values(), return_exceptions=True)
            for item in done:
                if isinstance(item, ToolResultBlock):
                    read_results.append(item)
                elif isinstance(item, BaseException):
                    logger.error("Unexpected error in background read task: %s", item)

        return read_results, list(self._write_calls)

    def pending_write_count(self) -> int:
        """Return number of write calls waiting for sequential execution."""
        return len(self._write_calls)


class StreamingToolCollector:
    """Collects completed tool calls and decides whether they can run immediately.

    A tool call is eligible for immediate (concurrent) execution when *both*:
    - ``tool.is_read_only(args)`` returns True
    - ``tool.is_concurrency_safe(args)`` returns True

    All other calls (write operations, unknown tools, or tools that are not
    concurrency-safe) are buffered and returned together via :meth:`flush_pending`.
    """

    def __init__(self, tool_registry: ToolRegistry) -> None:
        self._registry = tool_registry
        self._pending_writes: list[ParsedToolCall] = []

    def on_tool_complete(self, call: ParsedToolCall) -> ParsedToolCall | None:
        """A tool call finished parsing.

        If the tool is read-only and concurrency-safe, return it immediately
        for parallel execution.  Otherwise buffer it and return None.
        """
        tool = self._registry.get(call.name)
        if tool is not None and tool.is_read_only(call.args) and tool.is_concurrency_safe(call.args):
            return call
        self._pending_writes.append(call)
        return None

    def flush_pending(self) -> list[ParsedToolCall]:
        """Return all buffered calls and clear the internal buffer."""
        pending = self._pending_writes
        self._pending_writes = []
        return pending

    def has_pending(self) -> bool:
        """Return True if there are buffered (write/unsafe) calls waiting."""
        return len(self._pending_writes) > 0
