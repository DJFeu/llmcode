"""Tests for HIDA types."""
from __future__ import annotations

import pytest

from llm_code.hida.types import TaskProfile, TaskType


class TestTaskType:
    def test_has_all_10_types(self):
        expected = {
            "coding", "debugging", "reviewing", "planning",
            "testing", "refactoring", "research", "deployment",
            "documentation", "conversation",
        }
        actual = {t.value for t in TaskType}
        assert actual == expected

    def test_values_are_lowercase_strings(self):
        for t in TaskType:
            assert t.value == t.value.lower()
            assert isinstance(t.value, str)


class TestTaskProfile:
    def test_frozen(self):
        profile = TaskProfile(
            task_type=TaskType.CODING,
            confidence=0.95,
            tools=frozenset({"read_file", "write_file", "edit_file"}),
            memory_keys=frozenset({"project_stack", "coding_style"}),
            governance_categories=frozenset({"coding"}),
            load_full_prompt=False,
        )
        with pytest.raises(AttributeError):
            profile.confidence = 0.5  # type: ignore[misc]

    def test_fields(self):
        profile = TaskProfile(
            task_type=TaskType.DEBUGGING,
            confidence=0.8,
            tools=frozenset({"read_file", "bash", "grep_search"}),
            memory_keys=frozenset(),
            governance_categories=frozenset({"debugging"}),
            load_full_prompt=False,
        )
        assert profile.task_type == TaskType.DEBUGGING
        assert profile.confidence == 0.8
        assert "bash" in profile.tools
        assert profile.load_full_prompt is False

    def test_tools_is_frozenset(self):
        profile = TaskProfile(
            task_type=TaskType.CODING,
            confidence=1.0,
            tools=frozenset({"read_file"}),
            memory_keys=frozenset(),
            governance_categories=frozenset(),
            load_full_prompt=False,
        )
        assert isinstance(profile.tools, frozenset)
        assert isinstance(profile.memory_keys, frozenset)
        assert isinstance(profile.governance_categories, frozenset)

    def test_full_load_profile(self):
        """When confidence is low, load_full_prompt should be True."""
        profile = TaskProfile(
            task_type=TaskType.CONVERSATION,
            confidence=0.3,
            tools=frozenset(),
            memory_keys=frozenset(),
            governance_categories=frozenset(),
            load_full_prompt=True,
        )
        assert profile.load_full_prompt is True
