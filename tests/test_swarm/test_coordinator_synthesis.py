"""Tests for Coordinator synthesis-first and context overlap."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from llm_code.api.types import TextBlock
from llm_code.swarm.coordinator import Coordinator


# ------------------------------------------------------------------
# Synthesis
# ------------------------------------------------------------------

class TestSynthesis:
    @pytest.fixture
    def coordinator(self):
        manager = MagicMock()
        provider = MagicMock()
        config = MagicMock()
        config.model = "test-model"
        config.swarm = MagicMock(synthesis_enabled=True, max_members=5)
        return Coordinator(manager=manager, provider=provider, config=config)

    @pytest.mark.asyncio
    async def test_synthesis_returns_dict(self, coordinator):
        mock_response = MagicMock()
        mock_response.content = [TextBlock(text='{"known_facts": ["x"], "unknowns": ["y"], "should_delegate": true, "reason": "complex"}')]
        coordinator._provider.send_message = AsyncMock(return_value=mock_response)

        result = await coordinator._synthesize("Build a REST API")
        assert result is not None
        assert result["should_delegate"] is True

    @pytest.mark.asyncio
    async def test_synthesis_skip_delegation(self, coordinator):
        mock_response = MagicMock()
        mock_response.content = [TextBlock(text='{"known_facts": [], "unknowns": [], "should_delegate": false, "reason": "simple question"}')]
        coordinator._provider.send_message = AsyncMock(return_value=mock_response)

        result = await coordinator._synthesize("What is Python?")
        assert result is not None
        assert result["should_delegate"] is False

    @pytest.mark.asyncio
    async def test_synthesis_failure_returns_none(self, coordinator):
        coordinator._provider.send_message = AsyncMock(side_effect=Exception("network error"))
        result = await coordinator._synthesize("anything")
        assert result is None

    @pytest.mark.asyncio
    async def test_synthesis_disabled_returns_none(self):
        manager = MagicMock()
        provider = MagicMock()
        config = MagicMock()
        config.swarm = MagicMock(synthesis_enabled=False)
        coord = Coordinator(manager=manager, provider=provider, config=config)
        result = await coord._synthesize("anything")
        assert result is None

    @pytest.mark.asyncio
    async def test_orchestrate_skips_when_synthesis_says_no(self, coordinator):
        mock_response = MagicMock()
        mock_response.content = [TextBlock(text='{"should_delegate": false, "reason": "too simple"}')]
        coordinator._provider.send_message = AsyncMock(return_value=mock_response)

        result = await coordinator.orchestrate("What time is it?")
        assert "Skipping delegation" in result
        assert "too simple" in result


# ------------------------------------------------------------------
# Context overlap
# ------------------------------------------------------------------

class TestContextOverlap:
    def test_identical_text(self):
        score = Coordinator.context_overlap("fix the login bug", "fix the login bug")
        assert score == 1.0

    def test_no_overlap(self):
        score = Coordinator.context_overlap("python django web", "rust embedded firmware")
        assert score == 0.0

    def test_partial_overlap(self):
        score = Coordinator.context_overlap(
            "implement user authentication with JWT tokens",
            "add JWT token validation to the API",
        )
        assert 0.0 < score < 1.0

    def test_empty_next_task(self):
        score = Coordinator.context_overlap("some context", "")
        assert score == 0.0

    def test_empty_worker_context(self):
        score = Coordinator.context_overlap("", "some task")
        assert score == 0.0

    def test_cjk_overlap(self):
        score = Coordinator.context_overlap("實作使用者認證系統", "實作認證功能")
        assert score > 0.0


# ------------------------------------------------------------------
# Parse JSON object
# ------------------------------------------------------------------

class TestParseJsonObject:
    def test_clean_json(self):
        result = Coordinator._parse_json_object('{"key": "value"}')
        assert result == {"key": "value"}

    def test_markdown_fenced(self):
        result = Coordinator._parse_json_object('```json\n{"key": "value"}\n```')
        assert result == {"key": "value"}

    def test_invalid_json(self):
        result = Coordinator._parse_json_object("not json at all")
        assert result is None

    def test_json_array_rejected(self):
        result = Coordinator._parse_json_object('[1, 2, 3]')
        assert result is None
