"""2-layer task classifier: keyword matching first, LLM fallback second."""
from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from llm_code.hida.types import TaskProfile, TaskType

if TYPE_CHECKING:
    pass

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
