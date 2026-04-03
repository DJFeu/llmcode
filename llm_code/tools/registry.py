"""Tool registry for managing and dispatching tools."""
from __future__ import annotations

from llm_code.api.types import ToolDefinition
from llm_code.tools.base import Tool, ToolResult


class ToolRegistry:
    """Central registry for tools with lookup and execution."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool; raises ValueError if name already registered."""
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        """Return the tool with the given name, or None if not found."""
        return self._tools.get(name)

    def all_tools(self) -> tuple[Tool, ...]:
        """Return all registered tools as a tuple."""
        return tuple(self._tools.values())

    def definitions(self, allowed: set[str] | None = None) -> tuple[ToolDefinition, ...]:
        """Return ToolDefinitions, optionally filtered to allowed names."""
        tools = self._tools.values()
        if allowed is not None:
            tools = (t for t in tools if t.name in allowed)  # type: ignore[assignment]
        return tuple(t.to_definition() for t in tools)

    def definitions_with_deferred(
        self,
        allowed: set[str] | None = None,
        max_visible: int = 20,
    ) -> tuple[tuple[ToolDefinition, ...], int]:
        """Return (visible_definitions, deferred_count) using DeferredToolManager.

        Core tools are always visible; remaining tools fill slots up to
        max_visible; the rest are deferred.  Returns the visible definitions
        as a tuple and the count of deferred tools as an integer.
        """
        from llm_code.tools.deferred import DeferredToolManager

        all_defs = list(self.definitions(allowed=allowed))
        manager = DeferredToolManager()
        visible, deferred = manager.select_tools(all_defs, max_visible=max_visible)
        return tuple(visible), len(deferred)

    def execute(self, name: str, args: dict) -> ToolResult:
        """Execute a tool by name; returns is_error=True if tool not found."""
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(output=f"Tool '{name}' not found", is_error=True)
        return tool.execute(args)
