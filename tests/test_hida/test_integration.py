"""Integration tests for HIDA with ConversationRuntime."""
from __future__ import annotations



from llm_code.hida.classifier import TaskClassifier
from llm_code.hida.engine import HidaEngine
from llm_code.hida.profiles import DEFAULT_PROFILES
from llm_code.hida.types import TaskType
from llm_code.runtime.config import HidaConfig


class TestHidaIntegration:
    """Test that HIDA filtering reaches the prompt builder."""

    def test_engine_filters_tool_defs(self):
        """Verify registry.definitions(allowed=...) filters correctly."""
        from llm_code.tools.registry import ToolRegistry
        from llm_code.tools.base import Tool, ToolResult, PermissionLevel

        class FakeReadTool(Tool):
            @property
            def name(self): return "read_file"
            @property
            def description(self): return "Read"
            @property
            def input_schema(self): return {"type": "object", "properties": {}}
            @property
            def required_permission(self): return PermissionLevel.READ_ONLY
            def execute(self, args): return ToolResult(output="ok")

        class FakeBashTool(Tool):
            @property
            def name(self): return "bash"
            @property
            def description(self): return "Bash"
            @property
            def input_schema(self): return {"type": "object", "properties": {}}
            @property
            def required_permission(self): return PermissionLevel.FULL_ACCESS
            def execute(self, args): return ToolResult(output="ok")

        registry = ToolRegistry()
        registry.register(FakeReadTool())
        registry.register(FakeBashTool())

        # Full definitions
        all_defs = registry.definitions()
        assert len(all_defs) == 2

        # Filtered to only read_file
        filtered_defs = registry.definitions(allowed={"read_file"})
        assert len(filtered_defs) == 1
        assert filtered_defs[0].name == "read_file"

    def test_hida_config_disabled_means_no_filtering(self):
        """When HIDA is disabled, all tools should be loaded."""
        config = HidaConfig(enabled=False)
        assert config.enabled is False

    def test_classify_and_filter_pipeline(self):
        """Full pipeline: classify -> filter tools -> filter memory."""
        classifier = TaskClassifier(profiles=DEFAULT_PROFILES)
        engine = HidaEngine()

        # Classify a debugging message
        profile = classifier.classify_by_keywords("fix the crash in parser.py")
        assert profile is not None
        assert profile.task_type == TaskType.DEBUGGING

        # Filter tools
        all_tools = {"read_file", "write_file", "bash", "grep_search", "glob_search", "edit_file"}
        filtered_tools = engine.filter_tools(profile, all_tools)
        assert "bash" in filtered_tools
        assert "read_file" in filtered_tools
        # write_file and edit_file should NOT be in debugging profile
        assert "write_file" not in filtered_tools
        assert "edit_file" not in filtered_tools

        # Filter memory
        all_memory = {
            "known_issues": "Parser OOM on large files",
            "project_stack": "Python 3.12",
            "deployment_config": "Docker",
        }
        filtered_memory = engine.filter_memory(profile, all_memory)
        assert "known_issues" in filtered_memory
        assert "project_stack" in filtered_memory
        assert "deployment_config" not in filtered_memory
