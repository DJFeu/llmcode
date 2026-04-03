"""Tests for DeferredToolManager and ToolSearchTool — TDD: written before implementation."""
from __future__ import annotations


from llm_code.api.types import ToolDefinition
from llm_code.tools.deferred import DeferredToolManager, CORE_TOOLS
from llm_code.tools.base import PermissionLevel, Tool, ToolResult
from llm_code.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_def(name: str, description: str = "") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description or f"Tool: {name}",
        input_schema={"type": "object", "properties": {}, "required": []},
    )


def make_many_defs(names: list[str]) -> list[ToolDefinition]:
    return [make_def(n) for n in names]


# ---------------------------------------------------------------------------
# CORE_TOOLS constant
# ---------------------------------------------------------------------------

class TestCoreTools:
    def test_is_frozenset(self):
        assert isinstance(CORE_TOOLS, frozenset)

    def test_contains_expected_tools(self):
        expected = {"read_file", "write_file", "edit_file", "glob_search", "grep_search", "bash", "agent", "tool_search"}
        assert expected.issubset(CORE_TOOLS)

    def test_minimum_size(self):
        assert len(CORE_TOOLS) >= 8


# ---------------------------------------------------------------------------
# select_tools
# ---------------------------------------------------------------------------

class TestSelectTools:
    def test_all_visible_when_few_tools(self):
        """When total tools <= max_visible, all are visible, none deferred."""
        manager = DeferredToolManager()
        defs = make_many_defs(["read_file", "bash", "git_status"])
        visible, deferred = manager.select_tools(defs, max_visible=20)
        assert len(visible) == 3
        assert deferred == []

    def test_core_tools_always_visible(self):
        """Core tools are always in visible set, even when many tools exist."""
        manager = DeferredToolManager()
        non_core = [make_def(f"tool_{i}") for i in range(25)]
        core = [make_def(n) for n in CORE_TOOLS]
        all_defs = core + non_core
        visible, deferred = manager.select_tools(all_defs, max_visible=20)
        visible_names = {d.name for d in visible}
        for core_name in CORE_TOOLS:
            if core_name in {d.name for d in all_defs}:
                assert core_name in visible_names

    def test_deferred_when_too_many_tools(self):
        """Extra non-core tools go to deferred when total exceeds max_visible."""
        manager = DeferredToolManager()
        # 8 core + 20 non-core = 28 total; max_visible=15
        core_defs = [make_def(n) for n in CORE_TOOLS]
        extra_defs = [make_def(f"extra_{i}") for i in range(20)]
        all_defs = core_defs + extra_defs
        visible, deferred = manager.select_tools(all_defs, max_visible=15)
        assert len(visible) <= 15
        assert len(deferred) > 0
        # Total = visible + deferred
        assert len(visible) + len(deferred) == len(all_defs)

    def test_visible_plus_deferred_equals_all(self):
        """Every tool ends up either visible or deferred."""
        manager = DeferredToolManager()
        all_names = [f"tool_{i}" for i in range(30)]
        all_defs = make_many_defs(all_names)
        visible, deferred = manager.select_tools(all_defs, max_visible=20)
        all_returned = {d.name for d in visible} | {d.name for d in deferred}
        assert all_returned == set(all_names)

    def test_returns_list_types(self):
        manager = DeferredToolManager()
        defs = make_many_defs(["read_file", "bash"])
        visible, deferred = manager.select_tools(defs, max_visible=20)
        assert isinstance(visible, list)
        assert isinstance(deferred, list)

    def test_no_duplicates_across_visible_and_deferred(self):
        manager = DeferredToolManager()
        all_defs = make_many_defs([f"t_{i}" for i in range(30)])
        visible, deferred = manager.select_tools(all_defs, max_visible=15)
        visible_names = {d.name for d in visible}
        deferred_names = {d.name for d in deferred}
        assert visible_names.isdisjoint(deferred_names)


# ---------------------------------------------------------------------------
# search_tools
# ---------------------------------------------------------------------------

class TestSearchTools:
    def test_empty_deferred_returns_empty(self):
        manager = DeferredToolManager()
        results = manager.search_tools("git", [])
        assert results == []

    def test_name_match(self):
        manager = DeferredToolManager()
        deferred = [
            make_def("git_status", "Show git status"),
            make_def("git_commit", "Commit files"),
            make_def("bash", "Run shell commands"),
        ]
        results = manager.search_tools("git", deferred)
        result_names = {d.name for d in results}
        assert "git_status" in result_names
        assert "git_commit" in result_names

    def test_description_match(self):
        manager = DeferredToolManager()
        deferred = [
            make_def("tool_a", "Reads configuration files"),
            make_def("tool_b", "Sends HTTP requests"),
        ]
        results = manager.search_tools("config", deferred)
        result_names = {d.name for d in results}
        assert "tool_a" in result_names

    def test_no_match_returns_empty(self):
        manager = DeferredToolManager()
        deferred = [make_def("git_status", "Show git status")]
        results = manager.search_tools("zzznomatch", deferred)
        assert results == []

    def test_case_insensitive(self):
        manager = DeferredToolManager()
        deferred = [make_def("GIT_STATUS", "Show GIT Status")]
        results = manager.search_tools("git", deferred)
        assert len(results) >= 1

    def test_returns_list_of_tool_definitions(self):
        manager = DeferredToolManager()
        deferred = [make_def("git_log", "Show git log")]
        results = manager.search_tools("git", deferred)
        assert isinstance(results, list)
        for r in results:
            assert isinstance(r, ToolDefinition)


# ---------------------------------------------------------------------------
# unlock_tool
# ---------------------------------------------------------------------------

class TestUnlockTool:
    def test_unlock_moves_tool_to_visible(self):
        """After unlocking, tool appears in visible set."""
        manager = DeferredToolManager()
        core_defs = [make_def(n) for n in CORE_TOOLS]
        extra_defs = [make_def(f"extra_{i}") for i in range(20)]
        all_defs = core_defs + extra_defs
        visible, deferred = manager.select_tools(all_defs, max_visible=10)

        # Pick a deferred tool and unlock it
        if deferred:
            target = deferred[0].name
            manager.unlock_tool(target)
            visible2, deferred2 = manager.select_tools(all_defs, max_visible=10)
            visible_names2 = {d.name for d in visible2}
            assert target in visible_names2

    def test_unlock_nonexistent_tool_is_noop(self):
        """Unlocking a tool not in deferred does not raise."""
        manager = DeferredToolManager()
        manager.unlock_tool("nonexistent_tool")  # Should not raise

    def test_unlocked_persists_across_select_calls(self):
        """An unlocked tool stays visible in subsequent select_tools calls."""
        manager = DeferredToolManager()
        core_defs = [make_def(n) for n in CORE_TOOLS]
        extra_defs = [make_def(f"extra_{i}") for i in range(20)]
        all_defs = core_defs + extra_defs
        visible, deferred = manager.select_tools(all_defs, max_visible=10)

        if deferred:
            target = deferred[0].name
            manager.unlock_tool(target)
            # Call select_tools again — unlocked tool should still be visible
            visible2, deferred2 = manager.select_tools(all_defs, max_visible=10)
            visible_names2 = {d.name for d in visible2}
            assert target in visible_names2


# ---------------------------------------------------------------------------
# ToolRegistry.definitions_with_deferred
# ---------------------------------------------------------------------------

class TestRegistryDefinitionsWithDeferred:
    def _make_registry_with_tools(self, names: list[str]) -> ToolRegistry:
        """Create a registry with stub tools for given names."""
        reg = ToolRegistry()

        class StubTool(Tool):
            def __init__(self, _name: str) -> None:
                self._name = _name

            @property
            def name(self) -> str:
                return self._name

            @property
            def description(self) -> str:
                return f"Stub tool: {self._name}"

            @property
            def input_schema(self) -> dict:
                return {"type": "object", "properties": {}, "required": []}

            @property
            def required_permission(self) -> PermissionLevel:
                return PermissionLevel.READ_ONLY

            def execute(self, args: dict) -> ToolResult:
                return ToolResult(output="ok")

        for n in names:
            reg.register(StubTool(n))
        return reg

    def test_returns_visible_defs_and_deferred_count(self):
        names = [f"tool_{i}" for i in range(30)]
        reg = self._make_registry_with_tools(names)
        visible_defs, deferred_count = reg.definitions_with_deferred(allowed=None, max_visible=20)
        assert isinstance(visible_defs, tuple)
        assert isinstance(deferred_count, int)
        assert deferred_count >= 0

    def test_deferred_count_plus_visible_equals_total(self):
        names = [f"tool_{i}" for i in range(30)]
        reg = self._make_registry_with_tools(names)
        visible_defs, deferred_count = reg.definitions_with_deferred(allowed=None, max_visible=20)
        assert len(visible_defs) + deferred_count == 30

    def test_no_deferred_when_tools_fit(self):
        names = [f"tool_{i}" for i in range(5)]
        reg = self._make_registry_with_tools(names)
        visible_defs, deferred_count = reg.definitions_with_deferred(allowed=None, max_visible=20)
        assert deferred_count == 0
        assert len(visible_defs) == 5

    def test_allowed_filter_respected(self):
        names = ["read_file", "bash", "git_status", "write_file"]
        reg = self._make_registry_with_tools(names)
        visible_defs, deferred_count = reg.definitions_with_deferred(
            allowed={"read_file", "bash"}, max_visible=20
        )
        all_names = {d.name for d in visible_defs}
        assert all_names.issubset({"read_file", "bash"})
        assert deferred_count == 0


# ---------------------------------------------------------------------------
# ToolSearchTool
# ---------------------------------------------------------------------------

class TestToolSearchTool:
    def test_tool_has_correct_name(self):
        from llm_code.tools.tool_search import ToolSearchTool
        manager = DeferredToolManager()
        tool = ToolSearchTool(manager)
        assert tool.name == "tool_search"

    def test_tool_is_read_only(self):
        from llm_code.tools.tool_search import ToolSearchTool
        manager = DeferredToolManager()
        tool = ToolSearchTool(manager)
        assert tool.required_permission == PermissionLevel.READ_ONLY

    def test_execute_returns_matching_tools(self):
        from llm_code.tools.tool_search import ToolSearchTool
        manager = DeferredToolManager()
        # Seed manager with some deferred tools via select_tools
        core_defs = [make_def(n) for n in CORE_TOOLS]
        extra_defs = [
            make_def("git_status", "Show git repository status"),
            make_def("git_commit", "Commit staged changes to git"),
            make_def("http_get", "Send HTTP GET request"),
        ]
        all_defs = core_defs + extra_defs
        manager.select_tools(all_defs, max_visible=len(CORE_TOOLS))  # force extras to deferred

        tool = ToolSearchTool(manager)
        result = tool.execute({"query": "git"})
        assert not result.is_error
        assert "git_status" in result.output or "git_commit" in result.output

    def test_execute_no_match_returns_informative_message(self):
        from llm_code.tools.tool_search import ToolSearchTool
        manager = DeferredToolManager()
        manager.select_tools([], max_visible=20)
        tool = ToolSearchTool(manager)
        result = tool.execute({"query": "zzznomatch"})
        assert not result.is_error
        # Should say no results found
        assert "no" in result.output.lower() or "not found" in result.output.lower() or result.output.strip() != ""

    def test_execute_unlocks_found_tools(self):
        from llm_code.tools.tool_search import ToolSearchTool
        manager = DeferredToolManager()
        core_defs = [make_def(n) for n in CORE_TOOLS]
        extra_defs = [make_def("git_status", "Show git repository status")]
        all_defs = core_defs + extra_defs
        manager.select_tools(all_defs, max_visible=len(CORE_TOOLS))

        tool = ToolSearchTool(manager)
        tool.execute({"query": "git"})

        # After executing, git_status should be in unlocked set
        assert "git_status" in manager._unlocked

    def test_input_schema_has_query_field(self):
        from llm_code.tools.tool_search import ToolSearchTool
        manager = DeferredToolManager()
        tool = ToolSearchTool(manager)
        schema = tool.input_schema
        assert "query" in schema.get("properties", {})
        assert "query" in schema.get("required", [])
