"""StreamingToolCollector: route tool calls to immediate execution or pending buffer."""
from __future__ import annotations

from llm_code.tools.parsing import ParsedToolCall
from llm_code.tools.registry import ToolRegistry


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
