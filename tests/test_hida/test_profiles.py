"""Tests for default HIDA profiles."""
from __future__ import annotations

from llm_code.hida.profiles import DEFAULT_PROFILES
from llm_code.hida.types import TaskProfile, TaskType


class TestDefaultProfiles:
    def test_every_task_type_has_a_profile(self):
        for t in TaskType:
            assert t in DEFAULT_PROFILES, f"Missing profile for {t.value}"

    def test_all_profiles_are_task_profiles(self):
        for t, profile in DEFAULT_PROFILES.items():
            assert isinstance(profile, TaskProfile), f"{t.value} profile is not TaskProfile"

    def test_coding_profile_has_file_tools(self):
        p = DEFAULT_PROFILES[TaskType.CODING]
        assert "read_file" in p.tools
        assert "write_file" in p.tools
        assert "edit_file" in p.tools

    def test_debugging_profile_has_bash(self):
        p = DEFAULT_PROFILES[TaskType.DEBUGGING]
        assert "bash" in p.tools
        assert "read_file" in p.tools

    def test_reviewing_profile_has_read_tools(self):
        p = DEFAULT_PROFILES[TaskType.REVIEWING]
        assert "read_file" in p.tools
        assert "grep_search" in p.tools

    def test_conversation_profile_has_minimal_tools(self):
        p = DEFAULT_PROFILES[TaskType.CONVERSATION]
        # Conversation may need no tools or just memory
        assert len(p.tools) <= 5

    def test_all_profiles_have_confidence_1(self):
        """Default profiles represent maximum confidence for their type."""
        for t, profile in DEFAULT_PROFILES.items():
            assert profile.confidence == 1.0, f"{t.value} should have confidence 1.0"

    def test_all_profiles_do_not_load_full_prompt(self):
        """Default profiles use filtered context, not full load."""
        for t, profile in DEFAULT_PROFILES.items():
            assert profile.load_full_prompt is False, f"{t.value} should not load full prompt"
