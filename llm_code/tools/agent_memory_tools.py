"""Subagent memory tools (v16 M2).

Three tiny tools — ``memory_read``, ``memory_write``, ``memory_list`` —
bound to a per-agent :class:`~llm_code.runtime.agent_memory.AgentMemoryView`.
They are appended to a subagent's tool registry by
``runtime.subagent_factory.make_subagent_runtime`` when
``profile.agent_memory_enabled`` is on.

Why a separate file
-------------------

The pattern matches every other small tool in :mod:`llm_code.tools`
(``memory_tools.py`` for global memory, ``cron_tools.py`` for cron,
etc.). Keeping these out of ``memory_tools.py`` avoids a name clash —
the existing module operates on the project-level memory store, while
these new tools speak to the *agent_id-scoped* store.

Risk mitigations
----------------

* The view is captured by closure; the tool instance carries no
  global state, so two subagents with different ``agent_id`` values
  never share a cell.
* Writes are bounded (64 KiB, enforced in ``AgentMemoryView.write``).
* The tool ``execute`` paths return ``ToolResult(is_error=True, ...)``
  for malformed inputs instead of raising — keeps the runtime's
  error-handling story consistent with other built-in tools.
"""
from __future__ import annotations

from llm_code.runtime.agent_memory import AgentMemoryView
from llm_code.tools.base import PermissionLevel, Tool, ToolResult


class MemoryReadTool(Tool):
    """Read a value from the spawning agent's memory cell."""

    def __init__(self, view: AgentMemoryView) -> None:
        self._view = view

    @property
    def name(self) -> str:
        return "memory_read"

    @property
    def description(self) -> str:
        return (
            "Read a value previously written to this agent's memory cell. "
            "Memory is scoped per agent_id and survives across spawns "
            "within the same session."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Memory key to read.",
                },
            },
            "required": ["key"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    def is_concurrency_safe(self, args: dict) -> bool:
        return True

    def execute(self, args: dict) -> ToolResult:
        key = (args or {}).get("key", "")
        if not isinstance(key, str) or not key:
            return ToolResult(
                output="memory_read requires a non-empty 'key' string.",
                is_error=True,
            )
        value = self._view.read(key)
        if value is None:
            return ToolResult(output=f"(not found) key={key!r}")
        return ToolResult(output=value)


class MemoryWriteTool(Tool):
    """Write a value to the spawning agent's memory cell."""

    def __init__(self, view: AgentMemoryView) -> None:
        self._view = view

    @property
    def name(self) -> str:
        return "memory_write"

    @property
    def description(self) -> str:
        return (
            "Write a value to this agent's memory cell. The value can be "
            "read back via memory_read in this or a future spawn that "
            "shares the same agent_id."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "value": {"type": "string"},
            },
            "required": ["key", "value"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    def is_concurrency_safe(self, args: dict) -> bool:
        return False

    def execute(self, args: dict) -> ToolResult:
        args = args or {}
        key = args.get("key", "")
        value = args.get("value", "")
        if not isinstance(key, str) or not key:
            return ToolResult(
                output="memory_write requires a non-empty 'key' string.",
                is_error=True,
            )
        if not isinstance(value, str):
            return ToolResult(
                output="memory_write requires a string 'value'.",
                is_error=True,
            )
        try:
            self._view.write(key, value)
        except ValueError as exc:
            return ToolResult(output=str(exc), is_error=True)
        return ToolResult(output=f"ok: stored key={key!r} ({len(value)} bytes)")


class MemoryListTool(Tool):
    """List all keys in the spawning agent's memory cell."""

    def __init__(self, view: AgentMemoryView) -> None:
        self._view = view

    @property
    def name(self) -> str:
        return "memory_list"

    @property
    def description(self) -> str:
        return "List the keys stored in this agent's memory cell."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {},
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    def is_concurrency_safe(self, args: dict) -> bool:
        return True

    def execute(self, args: dict) -> ToolResult:
        keys = self._view.list_keys()
        if not keys:
            return ToolResult(output="(empty)")
        return ToolResult(output="\n".join(keys))


def build_memory_tools(view: AgentMemoryView) -> tuple[Tool, ...]:
    """Return the three memory tools bound to *view* in canonical order."""
    return (
        MemoryReadTool(view),
        MemoryWriteTool(view),
        MemoryListTool(view),
    )
