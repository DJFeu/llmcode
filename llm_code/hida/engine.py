"""HIDA engine: filters tools, memory, and prompt sections based on TaskProfile."""
from __future__ import annotations

from llm_code.hida.types import TaskProfile


class HidaEngine:
    """Applies a TaskProfile to filter context before prompt building."""

    def filter_tools(
        self, profile: TaskProfile, available_tools: set[str]
    ) -> set[str]:
        """Return the subset of tools allowed by the profile.

        If load_full_prompt is True, returns all available tools.
        """
        if profile.load_full_prompt:
            return set(available_tools)
        return profile.tools & available_tools

    def filter_memory(
        self, profile: TaskProfile, all_memory: dict[str, str]
    ) -> dict[str, str]:
        """Return the subset of memory entries relevant to the profile.

        If load_full_prompt is True, returns all memory entries.
        """
        if profile.load_full_prompt:
            return dict(all_memory)
        return {k: v for k, v in all_memory.items() if k in profile.memory_keys}

    def build_summary(self, profile: TaskProfile) -> str:
        """Build a human-readable summary of the current classification.

        Used by the /hida slash command.
        """
        if profile.load_full_prompt:
            return (
                f"Task: {profile.task_type.value} | "
                f"Confidence: {profile.confidence:.2f} | "
                f"Mode: full context load"
            )
        return (
            f"Task: {profile.task_type.value} | "
            f"Confidence: {profile.confidence:.2f} | "
            f"Tools: {len(profile.tools)} | "
            f"Memory keys: {len(profile.memory_keys)} | "
            f"Categories: {', '.join(sorted(profile.governance_categories)) or 'none'}"
        )
