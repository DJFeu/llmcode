"""Tests for HidaEngine context filtering."""
from __future__ import annotations


from llm_code.hida.engine import HidaEngine
from llm_code.hida.types import TaskProfile, TaskType


class TestToolFiltering:
    def test_filters_tools_to_profile_set(self):
        engine = HidaEngine()
        profile = TaskProfile(
            task_type=TaskType.CODING,
            confidence=0.9,
            tools=frozenset({"read_file", "write_file"}),
            memory_keys=frozenset(),
            governance_categories=frozenset(),
            load_full_prompt=False,
        )
        all_tools = {"read_file", "write_file", "bash", "grep_search", "glob_search"}
        filtered = engine.filter_tools(profile, all_tools)
        assert filtered == {"read_file", "write_file"}

    def test_full_load_returns_all_tools(self):
        engine = HidaEngine()
        profile = TaskProfile(
            task_type=TaskType.CONVERSATION,
            confidence=0.0,
            tools=frozenset(),
            memory_keys=frozenset(),
            governance_categories=frozenset(),
            load_full_prompt=True,
        )
        all_tools = {"read_file", "write_file", "bash", "grep_search"}
        filtered = engine.filter_tools(profile, all_tools)
        assert filtered == all_tools

    def test_missing_tools_ignored(self):
        """Profile requests tools that don't exist — no error, just skip."""
        engine = HidaEngine()
        profile = TaskProfile(
            task_type=TaskType.CODING,
            confidence=0.9,
            tools=frozenset({"read_file", "nonexistent_tool"}),
            memory_keys=frozenset(),
            governance_categories=frozenset(),
            load_full_prompt=False,
        )
        all_tools = {"read_file", "write_file"}
        filtered = engine.filter_tools(profile, all_tools)
        assert filtered == {"read_file"}


class TestMemoryFiltering:
    def test_filters_memory_to_profile_keys(self):
        engine = HidaEngine()
        profile = TaskProfile(
            task_type=TaskType.CODING,
            confidence=0.9,
            tools=frozenset(),
            memory_keys=frozenset({"project_stack", "coding_style"}),
            governance_categories=frozenset(),
            load_full_prompt=False,
        )
        all_memory = {
            "project_stack": "Python + FastAPI",
            "coding_style": "PEP 8",
            "deployment_config": "Docker on GCP",
            "known_issues": "Memory leak in parser",
        }
        filtered = engine.filter_memory(profile, all_memory)
        assert filtered == {
            "project_stack": "Python + FastAPI",
            "coding_style": "PEP 8",
        }

    def test_full_load_returns_all_memory(self):
        engine = HidaEngine()
        profile = TaskProfile(
            task_type=TaskType.CONVERSATION,
            confidence=0.0,
            tools=frozenset(),
            memory_keys=frozenset(),
            governance_categories=frozenset(),
            load_full_prompt=True,
        )
        all_memory = {"a": "1", "b": "2"}
        filtered = engine.filter_memory(profile, all_memory)
        assert filtered == all_memory

    def test_empty_memory(self):
        engine = HidaEngine()
        profile = TaskProfile(
            task_type=TaskType.CODING,
            confidence=0.9,
            tools=frozenset(),
            memory_keys=frozenset({"project_stack"}),
            governance_categories=frozenset(),
            load_full_prompt=False,
        )
        filtered = engine.filter_memory(profile, {})
        assert filtered == {}


class TestContextSummary:
    def test_summary_includes_task_type(self):
        engine = HidaEngine()
        profile = TaskProfile(
            task_type=TaskType.DEBUGGING,
            confidence=0.85,
            tools=frozenset({"bash", "read_file"}),
            memory_keys=frozenset({"known_issues"}),
            governance_categories=frozenset({"debugging"}),
            load_full_prompt=False,
        )
        summary = engine.build_summary(profile)
        assert "debugging" in summary.lower()
        assert "0.85" in summary

    def test_full_load_summary(self):
        engine = HidaEngine()
        profile = TaskProfile(
            task_type=TaskType.CONVERSATION,
            confidence=0.0,
            tools=frozenset(),
            memory_keys=frozenset(),
            governance_categories=frozenset(),
            load_full_prompt=True,
        )
        summary = engine.build_summary(profile)
        assert "full" in summary.lower()
