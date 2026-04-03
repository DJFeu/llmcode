"""Tests for TaskClassifier with 2-layer classification."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from llm_code.hida.classifier import TaskClassifier
from llm_code.hida.profiles import DEFAULT_PROFILES
from llm_code.hida.types import TaskProfile, TaskType


class TestKeywordClassification:
    """Layer 1: keyword matching (no LLM needed)."""

    def test_coding_keywords(self):
        classifier = TaskClassifier(profiles=DEFAULT_PROFILES)
        result = classifier.classify_by_keywords("write a function to sort a list")
        assert result is not None
        assert result.task_type == TaskType.CODING
        assert result.confidence >= 0.8

    def test_debugging_keywords(self):
        classifier = TaskClassifier(profiles=DEFAULT_PROFILES)
        result = classifier.classify_by_keywords("fix this bug in the login handler")
        assert result is not None
        assert result.task_type == TaskType.DEBUGGING

    def test_reviewing_keywords(self):
        classifier = TaskClassifier(profiles=DEFAULT_PROFILES)
        result = classifier.classify_by_keywords("review this pull request")
        assert result is not None
        assert result.task_type == TaskType.REVIEWING

    def test_testing_keywords(self):
        classifier = TaskClassifier(profiles=DEFAULT_PROFILES)
        result = classifier.classify_by_keywords("write unit tests for the parser module")
        assert result is not None
        assert result.task_type == TaskType.TESTING

    def test_planning_keywords(self):
        classifier = TaskClassifier(profiles=DEFAULT_PROFILES)
        result = classifier.classify_by_keywords("create an implementation plan for the auth system")
        assert result is not None
        assert result.task_type == TaskType.PLANNING

    def test_refactoring_keywords(self):
        classifier = TaskClassifier(profiles=DEFAULT_PROFILES)
        result = classifier.classify_by_keywords("refactor the database module to use async")
        assert result is not None
        assert result.task_type == TaskType.REFACTORING

    def test_deployment_keywords(self):
        classifier = TaskClassifier(profiles=DEFAULT_PROFILES)
        result = classifier.classify_by_keywords("deploy this to production")
        assert result is not None
        assert result.task_type == TaskType.DEPLOYMENT

    def test_documentation_keywords(self):
        classifier = TaskClassifier(profiles=DEFAULT_PROFILES)
        result = classifier.classify_by_keywords("write documentation for the API endpoints")
        assert result is not None
        assert result.task_type == TaskType.DOCUMENTATION

    def test_research_keywords(self):
        classifier = TaskClassifier(profiles=DEFAULT_PROFILES)
        result = classifier.classify_by_keywords("research the best approach for caching")
        assert result is not None
        assert result.task_type == TaskType.RESEARCH

    def test_ambiguous_returns_none(self):
        classifier = TaskClassifier(profiles=DEFAULT_PROFILES)
        result = classifier.classify_by_keywords("hello, how are you?")
        assert result is None

    def test_conversation_greeting(self):
        classifier = TaskClassifier(profiles=DEFAULT_PROFILES)
        result = classifier.classify_by_keywords("hi there")
        # Greetings may return None (ambiguous) or conversation
        if result is not None:
            assert result.task_type == TaskType.CONVERSATION


class TestLLMFallbackClassification:
    """Layer 2: LLM-based classification for ambiguous inputs."""

    @pytest.mark.asyncio
    async def test_llm_fallback_returns_profile(self):
        mock_provider = AsyncMock()
        mock_provider.complete.return_value = "coding"

        classifier = TaskClassifier(profiles=DEFAULT_PROFILES)
        result = await classifier.classify_by_llm("add error handling to the parser", mock_provider)
        assert result is not None
        assert isinstance(result, TaskProfile)

    @pytest.mark.asyncio
    async def test_llm_fallback_invalid_response_returns_none(self):
        mock_provider = AsyncMock()
        mock_provider.complete.return_value = "unknown_category"

        classifier = TaskClassifier(profiles=DEFAULT_PROFILES)
        result = await classifier.classify_by_llm("something weird", mock_provider)
        assert result is None

    @pytest.mark.asyncio
    async def test_llm_fallback_exception_returns_none(self):
        mock_provider = AsyncMock()
        mock_provider.complete.side_effect = Exception("API error")

        classifier = TaskClassifier(profiles=DEFAULT_PROFILES)
        result = await classifier.classify_by_llm("classify this", mock_provider)
        assert result is None


class TestFullClassify:
    """Full 2-layer classify: keyword first, LLM fallback."""

    @pytest.mark.asyncio
    async def test_keyword_match_skips_llm(self):
        mock_provider = AsyncMock()
        classifier = TaskClassifier(profiles=DEFAULT_PROFILES)
        result = await classifier.classify("fix the crash in main.py", provider=mock_provider)
        assert result.task_type == TaskType.DEBUGGING
        mock_provider.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_ambiguous_falls_through_to_llm(self):
        mock_provider = AsyncMock()
        mock_provider.complete.return_value = "planning"
        classifier = TaskClassifier(profiles=DEFAULT_PROFILES)
        result = await classifier.classify("let's think about the next steps", provider=mock_provider)
        assert result.task_type == TaskType.PLANNING
        mock_provider.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_provider_returns_full_load(self):
        """Without a provider, ambiguous input returns full-load profile."""
        classifier = TaskClassifier(profiles=DEFAULT_PROFILES)
        result = await classifier.classify("something vague", provider=None)
        assert result.load_full_prompt is True
