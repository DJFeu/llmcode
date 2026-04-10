"""Tool registry for managing and dispatching tools."""
from __future__ import annotations

from typing import Iterable

from llm_code.api.types import ToolDefinition
from llm_code.tools.base import Tool, ToolResult


def _is_gpt_codex(model: str) -> bool:
    """Detect GPT-Codex / GPT-5 models that prefer unified-diff editing."""
    m = model.lower()
    return ("gpt-" in m or "codex" in m or "/gpt" in m or m.startswith("gpt")) and "oss" not in m


def _filter_by_model(tools: Iterable[Tool], model: str) -> list[Tool]:
    """Apply model-specific tool selection.

    GPT/Codex: prefer apply_patch when available, hide edit_file
    Other models: prefer edit_file, hide apply_patch when both exist

    If only one of edit_file/apply_patch is registered, no filtering happens.
    """
    tool_list = list(tools)
    names = {t.name for t in tool_list}
    has_apply_patch = "apply_patch" in names
    has_edit = "edit_file" in names

    if not (has_apply_patch and has_edit):
        return tool_list  # only one available, nothing to filter

    if _is_gpt_codex(model):
        # GPT prefers apply_patch
        return [t for t in tool_list if t.name != "edit_file"]
    else:
        # Other models prefer edit_file
        return [t for t in tool_list if t.name != "apply_patch"]


class ToolRegistry:
    """Central registry for tools with lookup and execution."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool; raises ValueError if name already registered."""
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> bool:
        """Remove a tool by name. Returns True if removed, False if absent.

        Wave2-5: used by the plugin executor to unload tools when a
        plugin is disabled or when rolling back a failed load. Safe
        to call on an unknown name (returns False) so the caller
        doesn't need to track which names made it into the registry
        before the error.
        """
        if name in self._tools:
            del self._tools[name]
            return True
        return False

    def get(self, name: str) -> Tool | None:
        """Return the tool with the given name, or None if not found."""
        return self._tools.get(name)

    def all_tools(self) -> tuple[Tool, ...]:
        """Return all registered tools as a tuple."""
        return tuple(self._tools.values())

    def filtered(
        self,
        allowed: set[str] | frozenset[str] | None,
        *,
        disallowed: frozenset[str] | None = None,
        is_builtin: bool = True,
        is_async: bool = False,
        is_teammate: bool = False,
    ) -> "ToolRegistry":
        """Return a new ToolRegistry filtered by *allowed* and agent context.

        Sentinel convention for *allowed*:
          * ``None``           -> unrestricted; new registry starts from every
            tool the parent has (full clone of references).
          * ``frozenset()``    -> deny-all; new registry is empty.
          * non-empty set      -> strict whitelist; only listed names that
            exist in the parent are included.

        When *is_builtin*, *is_async*, or *is_teammate* are specified, an
        additional multi-stage filter is applied via
        :func:`~llm_code.tools.tool_categories.filter_tools_for_agent`.
        This implements the six-stage permission model borrowed from
        claude-code (MCP bypass → global deny → custom deny → async allow
        → teammate extras).

        *disallowed* is an optional explicit deny-set applied **after** all
        other filtering (agent frontmatter ``disallowed_tools``).

        The returned registry is always a fresh ``ToolRegistry`` instance;
        mutating it does not affect the parent.  Tool instances themselves
        are shared by reference.
        """
        from llm_code.tools.tool_categories import filter_tools_for_agent

        child = ToolRegistry()

        # Step 1: apply role whitelist (sentinel convention)
        if allowed is None:
            candidates = dict(self._tools)
        elif not allowed:
            # deny-all sentinel
            return child
        else:
            candidates = {
                name: self._tools[name]
                for name in allowed
                if name in self._tools
            }

        # Step 2: multi-stage agent filter (pure function, no mutation)
        surviving = filter_tools_for_agent(
            frozenset(candidates),
            is_builtin=is_builtin,
            is_async=is_async,
            is_teammate=is_teammate,
        )

        # Step 3: explicit disallowed set (from agent frontmatter)
        for name in surviving:
            if disallowed and name in disallowed:
                continue
            tool = candidates.get(name)
            if tool is not None:
                child._tools[name] = tool

        return child

    def definitions(
        self,
        allowed: set[str] | None = None,
        model: str | None = None,
    ) -> tuple[ToolDefinition, ...]:
        """Return ToolDefinitions, optionally filtered.

        Args:
            allowed: If provided, only tools with names in this set are returned.
            model: If provided, applies model-specific tool selection.
                   GPT models prefer apply_patch over edit/write when both exist.
        """
        tools = self._tools.values()
        if allowed is not None:
            tools = (t for t in tools if t.name in allowed)  # type: ignore[assignment]
        if model:
            tools = _filter_by_model(tools, model)
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
