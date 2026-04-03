"""ToolSearchTool — lets the LLM discover and unlock deferred tools."""
from __future__ import annotations

from llm_code.tools.base import PermissionLevel, Tool, ToolResult

if True:
    # Avoid circular imports; DeferredToolManager is a pure data class
    from llm_code.tools.deferred import DeferredToolManager


class ToolSearchTool(Tool):
    """Search deferred tools by name/description and unlock matching ones."""

    def __init__(self, manager: "DeferredToolManager") -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "tool_search"

    @property
    def description(self) -> str:
        return (
            "Search for additional tools that are not currently visible. "
            "Provide a query string to find tools by name or description. "
            "Matching tools will be unlocked and available in subsequent turns."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query to match against tool names and descriptions.",
                }
            },
            "required": ["query"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    def execute(self, args: dict) -> ToolResult:
        query = args.get("query", "")
        deferred = self._manager._deferred
        matches = self._manager.search_tools(query, deferred)

        if not matches:
            return ToolResult(
                output=f"No tools found matching '{query}'. "
                "Try a different search term or use a broader query.",
            )

        # Unlock all matching tools
        for d in matches:
            self._manager.unlock_tool(d.name)

        lines = [f"Found {len(matches)} tool(s) matching '{query}' (now unlocked):"]
        for d in matches:
            lines.append(f"  - {d.name}: {d.description}")

        return ToolResult(output="\n".join(lines))
