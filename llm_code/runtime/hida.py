"""HIDA: Hierarchical Intent-Driven Architecture for dynamic context loading.

Phase 5.5 of the 2026-04-11 architecture refactor: this module consolidates
the four files that used to live under ``llm_code/hida/`` into a single
runtime module. The old package is kept as a backward-compatibility shim so
existing imports (and the large ``tests/test_hida/`` suite) keep working.

HIDA classifies each user turn into a ``TaskType``, looks up a matching
``TaskProfile``, and lets the runtime filter tools / memory / prompt
sections before the next completion. Everything here is pure
data + logic — no I/O, no async side effects beyond the LLM classifier
fallback that callers opt into explicitly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any


# ── Types ───────────────────────────────────────────────────────────────


class TaskType(Enum):
    CODING = "coding"
    DEBUGGING = "debugging"
    REVIEWING = "reviewing"
    PLANNING = "planning"
    TESTING = "testing"
    REFACTORING = "refactoring"
    RESEARCH = "research"
    DEPLOYMENT = "deployment"
    DOCUMENTATION = "documentation"
    CONVERSATION = "conversation"


@dataclass(frozen=True)
class TaskProfile:
    task_type: TaskType
    confidence: float
    tools: frozenset[str]
    memory_keys: frozenset[str]
    governance_categories: frozenset[str]
    load_full_prompt: bool


# ── Default profiles ────────────────────────────────────────────────────

# Core tool sets shared across profiles
_FILE_READ = frozenset({"read_file", "glob_search", "grep_search"})
_FILE_WRITE = frozenset({"write_file", "edit_file"})
_SHELL = frozenset({"bash"})
_MEMORY = frozenset({"memory_store", "memory_recall", "memory_list"})
_GIT = frozenset({"git_diff", "git_log", "git_status"})
_AGENT = frozenset({"agent"})

DEFAULT_PROFILES: dict[TaskType, TaskProfile] = {
    TaskType.CODING: TaskProfile(
        task_type=TaskType.CODING,
        confidence=1.0,
        tools=_FILE_READ | _FILE_WRITE | _SHELL | _AGENT,
        memory_keys=frozenset({"project_stack", "coding_style", "architecture"}),
        governance_categories=frozenset({"coding"}),
        load_full_prompt=False,
    ),
    TaskType.DEBUGGING: TaskProfile(
        task_type=TaskType.DEBUGGING,
        confidence=1.0,
        tools=_FILE_READ | _SHELL | _AGENT,
        memory_keys=frozenset({"known_issues", "project_stack"}),
        governance_categories=frozenset({"debugging"}),
        load_full_prompt=False,
    ),
    TaskType.REVIEWING: TaskProfile(
        task_type=TaskType.REVIEWING,
        confidence=1.0,
        tools=_FILE_READ | _GIT,
        memory_keys=frozenset({"coding_style", "review_guidelines"}),
        governance_categories=frozenset({"reviewing"}),
        load_full_prompt=False,
    ),
    TaskType.PLANNING: TaskProfile(
        task_type=TaskType.PLANNING,
        confidence=1.0,
        tools=_FILE_READ | _MEMORY | _AGENT,
        memory_keys=frozenset({"architecture", "project_stack", "roadmap"}),
        governance_categories=frozenset({"planning"}),
        load_full_prompt=False,
    ),
    TaskType.TESTING: TaskProfile(
        task_type=TaskType.TESTING,
        confidence=1.0,
        tools=_FILE_READ | _FILE_WRITE | _SHELL,
        memory_keys=frozenset({"project_stack", "test_patterns"}),
        governance_categories=frozenset({"testing"}),
        load_full_prompt=False,
    ),
    TaskType.REFACTORING: TaskProfile(
        task_type=TaskType.REFACTORING,
        confidence=1.0,
        tools=_FILE_READ | _FILE_WRITE | _SHELL | _GIT,
        memory_keys=frozenset({"architecture", "coding_style"}),
        governance_categories=frozenset({"refactoring"}),
        load_full_prompt=False,
    ),
    TaskType.RESEARCH: TaskProfile(
        task_type=TaskType.RESEARCH,
        confidence=1.0,
        tools=_FILE_READ | _SHELL | _MEMORY | _AGENT,
        memory_keys=frozenset({"project_stack"}),
        governance_categories=frozenset({"research"}),
        load_full_prompt=False,
    ),
    TaskType.DEPLOYMENT: TaskProfile(
        task_type=TaskType.DEPLOYMENT,
        confidence=1.0,
        tools=_FILE_READ | _FILE_WRITE | _SHELL | _GIT,
        memory_keys=frozenset({"deployment_config", "infrastructure"}),
        governance_categories=frozenset({"deployment"}),
        load_full_prompt=False,
    ),
    TaskType.DOCUMENTATION: TaskProfile(
        task_type=TaskType.DOCUMENTATION,
        confidence=1.0,
        tools=_FILE_READ | _FILE_WRITE | _MEMORY,
        memory_keys=frozenset({"project_stack", "architecture"}),
        governance_categories=frozenset({"documentation"}),
        load_full_prompt=False,
    ),
    TaskType.CONVERSATION: TaskProfile(
        task_type=TaskType.CONVERSATION,
        confidence=1.0,
        tools=_MEMORY,
        memory_keys=frozenset(),
        governance_categories=frozenset({"conversation"}),
        load_full_prompt=False,
    ),
}


# ── Classifier ──────────────────────────────────────────────────────────

# Keyword patterns per task type — order matters (first match wins within a type)
# IMPORTANT: More specific types are listed first. CODING is a low-priority fallback.
# Patterns use exclusive keywords that are unambiguous for each type.
_KEYWORD_PATTERNS: dict[TaskType, list[re.Pattern[str]]] = {
    TaskType.DEBUGGING: [
        re.compile(r"\b(fix|bug|crash|traceback|exception|debug|broken|fails?|failing)\b", re.I),
    ],
    TaskType.TESTING: [
        re.compile(r"\b(tests?|unittest|pytest|coverage|spec|assert)\b", re.I),
    ],
    TaskType.REVIEWING: [
        re.compile(r"\b(review|code.?review|pull.?request|pr|diff|audit)\b", re.I),
    ],
    TaskType.REFACTORING: [
        re.compile(r"\b(refactor|restructure|reorganize|clean.?up|simplify|extract)\b", re.I),
    ],
    TaskType.PLANNING: [
        re.compile(r"\b(plan|roadmap|proposal|rfc)\b", re.I),
    ],
    TaskType.DEPLOYMENT: [
        re.compile(r"\b(deploy|release|ci/?cd|docker|kubernetes|k8s|production|staging)\b", re.I),
    ],
    TaskType.DOCUMENTATION: [
        re.compile(r"\b(document(?:ation)?|readme|docstring|jsdoc|api.?doc)\b", re.I),
    ],
    TaskType.RESEARCH: [
        re.compile(r"\b(research|investigate|explore|compare|evaluate|benchmark)\b", re.I),
    ],
    # CODING is a low-priority generic fallback — only unique code-creation keywords
    TaskType.CODING: [
        re.compile(r"\b(implement|build|function|class|endpoint)\b", re.I),
    ],
}

# Priority ordering: when scores are tied, higher-priority type wins.
# Specific types beat CODING (low priority) and CONVERSATION (lowest).
_TYPE_PRIORITY: dict[TaskType, int] = {
    TaskType.DEBUGGING: 10,
    TaskType.TESTING: 10,
    TaskType.REVIEWING: 10,
    TaskType.REFACTORING: 10,
    TaskType.PLANNING: 10,
    TaskType.DEPLOYMENT: 10,
    TaskType.DOCUMENTATION: 10,
    TaskType.RESEARCH: 10,
    TaskType.CODING: 5,
    TaskType.CONVERSATION: 1,
}

# Classification confidence for keyword matches
_KEYWORD_CONFIDENCE = 0.85

# LLM classification prompt
_CLASSIFY_PROMPT = """\
Classify the following user message into exactly one task type.
Respond with ONLY the task type name, nothing else.

Valid types: coding, debugging, reviewing, planning, testing, refactoring, research, deployment, documentation, conversation

User message: {message}

Task type:"""


_FULL_LOAD_PROFILE = TaskProfile(
    task_type=TaskType.CONVERSATION,
    confidence=0.0,
    tools=frozenset(),
    memory_keys=frozenset(),
    governance_categories=frozenset(),
    load_full_prompt=True,
)


class TaskClassifier:
    """Classifies user messages into task types using a 2-layer approach."""

    def __init__(
        self,
        profiles: dict[TaskType, TaskProfile],
        custom_patterns: dict[TaskType, list[re.Pattern[str]]] | None = None,
    ) -> None:
        self._profiles = profiles
        self._patterns = custom_patterns if custom_patterns is not None else _KEYWORD_PATTERNS

    def classify_by_keywords(self, message: str) -> TaskProfile | None:
        """Layer 1: Fast keyword-based classification.

        Returns a TaskProfile with keyword confidence, or None if ambiguous.
        """
        scores: dict[TaskType, int] = {}
        for task_type, patterns in self._patterns.items():
            for pattern in patterns:
                matches = pattern.findall(message)
                if matches:
                    scores[task_type] = scores.get(task_type, 0) + len(matches)

        if not scores:
            return None

        # Pick highest scoring type; if tie, use priority to break it.
        # If tied AND same priority, return None (ambiguous).
        sorted_scores = sorted(
            scores.items(),
            key=lambda x: (x[1], _TYPE_PRIORITY.get(x[0], 0)),
            reverse=True,
        )
        if len(sorted_scores) > 1:
            top_score, top_priority = sorted_scores[0][1], _TYPE_PRIORITY.get(sorted_scores[0][0], 0)
            second_score, second_priority = sorted_scores[1][1], _TYPE_PRIORITY.get(sorted_scores[1][0], 0)
            if top_score == second_score and top_priority == second_priority:
                return None

        best_type = sorted_scores[0][0]
        base_profile = self._profiles.get(best_type)
        if base_profile is None:
            return None

        return replace(base_profile, confidence=_KEYWORD_CONFIDENCE)

    async def classify_by_llm(
        self, message: str, provider: Any
    ) -> TaskProfile | None:
        """Layer 2: LLM-based classification for ambiguous inputs.

        Args:
            message: The user message to classify.
            provider: An LLM provider with a `complete(prompt)` async method.

        Returns:
            A TaskProfile if classification succeeds, None otherwise.
        """
        try:
            prompt = _CLASSIFY_PROMPT.format(message=message[:500])
            response = await provider.complete(prompt)
            task_name = response.strip().lower()

            # Try to match response to a TaskType
            try:
                task_type = TaskType(task_name)
            except ValueError:
                return None

            base_profile = self._profiles.get(task_type)
            if base_profile is None:
                return None

            return replace(base_profile, confidence=0.7)
        except Exception:
            return None

    async def classify(
        self,
        message: str,
        provider: Any | None = None,
        confidence_threshold: float = 0.6,
    ) -> TaskProfile:
        """Full 2-layer classification: keywords first, LLM fallback.

        Always returns a TaskProfile. Falls back to full-load profile
        when confidence is below threshold or classification fails.
        """
        # Layer 1: keyword matching
        result = self.classify_by_keywords(message)
        if result is not None and result.confidence >= confidence_threshold:
            return result

        # Layer 2: LLM fallback (only if provider available)
        if provider is not None:
            llm_result = await self.classify_by_llm(message, provider)
            if llm_result is not None and llm_result.confidence >= confidence_threshold:
                return llm_result

        # Fallback: full context load
        return _FULL_LOAD_PROFILE


# ── Engine ──────────────────────────────────────────────────────────────


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


__all__ = [
    "DEFAULT_PROFILES",
    "HidaEngine",
    "TaskClassifier",
    "TaskProfile",
    "TaskType",
]
