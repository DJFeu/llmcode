"""Deferred tool loading: keeps visible tool list small by hiding rarely-used tools."""
from __future__ import annotations

from llm_code.api.types import ToolDefinition

# Core tools always visible regardless of max_visible limit
CORE_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "write_file",
        "edit_file",
        "glob_search",
        "grep_search",
        "bash",
        "agent",
        "tool_search",
    }
)


class DeferredToolManager:
    """Manages splitting tool definitions into visible and deferred sets.

    Core tools are always visible. Additional tools are shown up to max_visible;
    the rest are deferred until the LLM calls tool_search to unlock them.
    """

    def __init__(self) -> None:
        self._unlocked: set[str] = set()
        # Track the last deferred list for search
        self._deferred: list[ToolDefinition] = []

    def select_tools(
        self,
        all_defs: list[ToolDefinition],
        max_visible: int = 20,
    ) -> tuple[list[ToolDefinition], list[ToolDefinition]]:
        """Partition tool definitions into (visible, deferred).

        Core tools and any unlocked tools are always visible. Non-core tools
        fill remaining slots up to max_visible; the rest go to deferred.

        Returns:
            A 2-tuple of (visible_defs, deferred_defs).
        """
        all_names = {d.name for d in all_defs}

        # Always-visible: core tools present in all_defs + previously unlocked
        always_visible_names = (CORE_TOOLS | self._unlocked) & all_names

        visible: list[ToolDefinition] = []
        deferred: list[ToolDefinition] = []

        for d in all_defs:
            if d.name in always_visible_names:
                visible.append(d)

        # Fill remaining slots with non-core, non-unlocked tools
        remaining_slots = max_visible - len(visible)
        for d in all_defs:
            if d.name not in always_visible_names:
                if remaining_slots > 0:
                    visible.append(d)
                    remaining_slots -= 1
                else:
                    deferred.append(d)

        # Store for search
        self._deferred = deferred
        return visible, deferred

    def search_tools(
        self, query: str, deferred: list[ToolDefinition]
    ) -> list[ToolDefinition]:
        """Fuzzy-match query against name and description of deferred tools.

        Matching is case-insensitive substring search against both the tool
        name and description.

        Returns:
            List of matching ToolDefinition objects.
        """
        q = query.lower()
        results: list[ToolDefinition] = []
        for d in deferred:
            if q in d.name.lower() or q in d.description.lower():
                results.append(d)
        return results

    def unlock_tool(self, name: str) -> None:
        """Mark a tool as unlocked so it appears in visible set on future calls."""
        self._unlocked.add(name)
