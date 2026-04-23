"""ToolExecutorComponent — the actual tool invocation stage.

Runs ``tool.execute(args)`` and returns the :class:`ToolResult`. Retries
and fallback are explicitly **not** handled here — they are the M3
Agent's job. The v12 design keeps this Component minimal so failure
modes stay readable and the Agent's retry policy is the single source
of truth.

Semantics
---------
- ``proceed=False`` or ``resolved_tool=None`` → synthesize an error
  :class:`ToolResult` tagged with ``metadata={"source":
  "tool_executor_gate"}`` so downstream observability can split real
  tool failures from upstream gate denials.
- ``cached_result is not None`` → emit the cached result verbatim and
  set ``executed=False``.
- Otherwise → call ``resolved_tool.execute(tool_args)`` (sync path) or
  ``await resolved_tool.execute_async(tool_args)`` (async path, M5).

Exceptions
----------
By default, exceptions raised by ``tool.execute`` propagate. Pass
``catch_errors=True`` at construction to turn them into an error
:class:`ToolResult` — the parity test harness uses that mode to
compare shapes against the legacy pipeline without letting test-time
bugs abort the suite.

M5 async awareness
------------------
When ``run_async`` is invoked and the resolved tool has
``is_async=True``, the component awaits ``tool.execute_async(args)``
directly. Sync tools are driven via the default bridge inside
:meth:`Tool.execute_async` which calls ``asyncio.to_thread(tool.execute,
args)`` — so the event loop stays responsive even when the tool itself
is blocking.

Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-async-pipeline.md Task 5.4
"""
from __future__ import annotations

import asyncio
from typing import Any, Protocol

from llm_code.engine.component import component, output_types
from llm_code.tools.base import ToolResult


class _Tool(Protocol):
    """Structural type — we only need ``execute`` (and optionally ``execute_async``)."""

    def execute(self, args: dict) -> ToolResult: ...


@component
@output_types(raw_result=ToolResult, executed=bool)
class ToolExecutorComponent:
    """Execute a resolved tool or pass through a cached result."""

    def __init__(self, *, catch_errors: bool = False) -> None:
        self._catch_errors = catch_errors

    def run(
        self,
        proceed: bool,
        resolved_tool: _Tool | None,
        tool_args: dict,
        cached_result: ToolResult | None,
    ) -> dict[str, Any]:
        """Dispatch to the tool or surface a cache/gate short-circuit."""
        # Cached result wins — the speculative cache already has the answer.
        if cached_result is not None:
            return {"raw_result": cached_result, "executed": False}

        if not proceed:
            return {
                "raw_result": ToolResult(
                    output="tool execution blocked by upstream gate",
                    is_error=True,
                    metadata={"source": "tool_executor_gate"},
                ),
                "executed": False,
            }

        if resolved_tool is None:
            return {
                "raw_result": ToolResult(
                    output="no tool resolved for execution",
                    is_error=True,
                    metadata={"source": "tool_executor_gate"},
                ),
                "executed": False,
            }

        try:
            result = resolved_tool.execute(tool_args)
        except BaseException as exc:
            if not self._catch_errors:
                raise
            return {
                "raw_result": ToolResult(
                    output=f"tool execution raised: {exc!r}",
                    is_error=True,
                    metadata={"source": "tool_executor_exception"},
                ),
                "executed": False,
            }
        return {"raw_result": result, "executed": True}

    async def run_async(
        self,
        proceed: bool,
        resolved_tool: _Tool | None,
        tool_args: dict,
        cached_result: ToolResult | None,
    ) -> dict[str, Any]:
        """Async-native tool dispatch.

        Awaits ``resolved_tool.execute_async`` when present; falls back
        to an :func:`asyncio.to_thread` wrapper over ``execute`` when
        the tool is sync-only. Cache short-circuit and gate-denial paths
        mirror :meth:`run` bit-for-bit so both surfaces are parity-equal.
        """
        if cached_result is not None:
            return {"raw_result": cached_result, "executed": False}

        if not proceed:
            return {
                "raw_result": ToolResult(
                    output="tool execution blocked by upstream gate",
                    is_error=True,
                    metadata={"source": "tool_executor_gate"},
                ),
                "executed": False,
            }

        if resolved_tool is None:
            return {
                "raw_result": ToolResult(
                    output="no tool resolved for execution",
                    is_error=True,
                    metadata={"source": "tool_executor_gate"},
                ),
                "executed": False,
            }

        try:
            execute_async = getattr(resolved_tool, "execute_async", None)
            if execute_async is not None:
                # Tool.execute_async bridges sync execute onto a thread
                # when is_async is False, so this path handles both.
                result = await execute_async(tool_args)
            else:
                # Tool predates M5 — no execute_async; bridge manually.
                result = await asyncio.to_thread(resolved_tool.execute, tool_args)
        except BaseException as exc:
            if not self._catch_errors:
                raise
            return {
                "raw_result": ToolResult(
                    output=f"tool execution raised: {exc!r}",
                    is_error=True,
                    metadata={"source": "tool_executor_exception"},
                ),
                "executed": False,
            }
        return {"raw_result": result, "executed": True}
