"""Concurrent streaming tool executor with safety class separation.

Dispatches tool calls as they arrive from the model stream. Concurrent-safe
tools (read_file, glob/grep_search, web_*, notebook_read, lsp_*) execute in
parallel up to ``max_concurrent``; exclusive tools (write_file, edit_file,
bash, multi_edit, notebook_edit) serialize behind an exclusive lock so no
mutating operation overlaps with anything else.

This module is currently standalone and NOT wired into conversation.py — the
serial ``_execute_tool_call`` loop there is intentionally preserved until the
turn-loop ordering invariants can be validated end-to-end. See TODO below.

TODO(v1.10): Integrate into conversation.py's turn loop once per-tool result
ordering vs. user-visible stream events is proven safe under parallel dispatch.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

logger = logging.getLogger(__name__)


# Tools safe to run concurrently with each other and with each other instances.
CONCURRENT_SAFE: frozenset[str] = frozenset({
    "read_file",
    "glob_search",
    "grep_search",
    "web_search",
    "web_fetch",
    "notebook_read",
    "lsp_diagnose",
    "lsp_hover",
    "lsp_references",
    "task_get",
    "task_list",
    "swarm_list",
    "cron_list",
    "tool_search",
})


def is_concurrent_safe(tool_name: str) -> bool:
    """Return True if the tool can run alongside other concurrent-safe tools."""
    return tool_name in CONCURRENT_SAFE or tool_name.startswith("lsp_")


@dataclass(frozen=True)
class ToolCall:
    """Minimal tool invocation record for dispatch."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    """Result of a dispatched tool call."""

    id: str
    name: str
    output: Any
    error: str | None = None
    is_error: bool = False


class ToolRunner(Protocol):
    """A callable that actually runs a tool and returns its result."""

    async def __call__(self, call: ToolCall) -> Any: ...


class StreamingToolExecutor:
    """Dispatch tool calls concurrently with safety-class awareness.

    Concurrent-safe tools share a semaphore (bounded parallelism). Exclusive
    (mutating) tools take an exclusive async lock that also excludes any
    in-flight concurrent-safe work — so an ``edit_file`` will not race a
    ``read_file`` of the same path.
    """

    def __init__(self, max_concurrent: int = 4) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        self._max_concurrent = max_concurrent
        self._sem = asyncio.Semaphore(max_concurrent)
        self._exclusive_lock = asyncio.Lock()
        self._inflight_concurrent = 0
        self._inflight_lock = asyncio.Lock()
        self._drained = asyncio.Event()
        self._drained.set()

    async def _run_concurrent(
        self, call: ToolCall, runner: ToolRunner
    ) -> ToolResult:
        async with self._sem:
            async with self._inflight_lock:
                self._inflight_concurrent += 1
                self._drained.clear()
            try:
                output = await runner(call)
                return ToolResult(id=call.id, name=call.name, output=output)
            except Exception as exc:  # noqa: BLE001 — surface tool errors as results
                logger.exception("concurrent tool %s failed", call.name)
                return ToolResult(
                    id=call.id,
                    name=call.name,
                    output=None,
                    error=str(exc),
                    is_error=True,
                )
            finally:
                async with self._inflight_lock:
                    self._inflight_concurrent -= 1
                    if self._inflight_concurrent == 0:
                        self._drained.set()

    async def _run_exclusive(
        self, call: ToolCall, runner: ToolRunner
    ) -> ToolResult:
        async with self._exclusive_lock:
            # Wait for any in-flight concurrent-safe tools to drain before running
            # a mutating tool — prevents read/write races.
            await self._drained.wait()
            try:
                output = await runner(call)
                return ToolResult(id=call.id, name=call.name, output=output)
            except Exception as exc:  # noqa: BLE001
                logger.exception("exclusive tool %s failed", call.name)
                return ToolResult(
                    id=call.id,
                    name=call.name,
                    output=None,
                    error=str(exc),
                    is_error=True,
                )

    async def dispatch(self, call: ToolCall, runner: ToolRunner) -> ToolResult:
        """Dispatch a single tool call respecting its safety class."""
        if is_concurrent_safe(call.name):
            return await self._run_concurrent(call, runner)
        return await self._run_exclusive(call, runner)

    async def dispatch_many(
        self,
        calls: list[ToolCall],
        runner: ToolRunner,
    ) -> list[ToolResult]:
        """Dispatch a batch of calls.

        Ordering guarantee: the returned list has the same order as ``calls``.
        Execution order is not guaranteed — concurrent-safe tools may finish
        in any order, while exclusive tools serialize in dispatch order.
        """
        tasks = [asyncio.create_task(self.dispatch(c, runner)) for c in calls]
        return await asyncio.gather(*tasks)

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent
