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


# ------------------------------------------------------------------
# Subagent task_id resume
# ------------------------------------------------------------------

class TestSubagentResume:
    @pytest.fixture
    def coordinator(self):
        manager = MagicMock()
        provider = MagicMock()
        config = MagicMock()
        config.model = "test-model"
        config.swarm = MagicMock(synthesis_enabled=True, max_members=5)
        return Coordinator(manager=manager, provider=provider, config=config)

    @pytest.mark.asyncio
    async def test_resume_uses_existing_members(self, coordinator):
        # Mock synthesis to return delegate=true
        synthesis_response = MagicMock()
        synthesis_response.content = [TextBlock(text='{"should_delegate": true, "reason": "x"}')]
        # Mock aggregate response (only called once — no decompose since we resume)
        aggregate_response = MagicMock()
        aggregate_response.content = [TextBlock(text="Resumed work complete.")]
        coordinator._provider.send_message = AsyncMock(side_effect=[synthesis_response, aggregate_response])

        # Mock existing member
        existing = MagicMock(id="m1", role="coder", task="continue work")
        coordinator._manager.get_member = MagicMock(return_value=existing)
        coordinator._manager.create_member = AsyncMock()  # should NOT be called
        coordinator._manager.mailbox = MagicMock()
        coordinator._manager.mailbox.receive_and_clear = MagicMock(return_value=[
            MagicMock(text="DONE")
        ])
        coordinator.TIMEOUT = 1.0
        coordinator.POLL_INTERVAL = 0.05

        result = await coordinator.orchestrate("more work", resume_member_ids=["m1"])

        # Should NOT have called create_member (used resume)
        coordinator._manager.create_member.assert_not_called()
        coordinator._manager.get_member.assert_called_with("m1")
        assert "Resumable swarm member IDs" in result
        assert "m1" in result

    @pytest.mark.asyncio
    async def test_resume_falls_through_when_member_missing(self, coordinator):
        synthesis_response = MagicMock()
        synthesis_response.content = [TextBlock(text='{"should_delegate": true, "reason": "x"}')]
        decompose_response = MagicMock()
        decompose_response.content = [TextBlock(text='[{"role": "coder", "task": "fresh task"}]')]
        aggregate_response = MagicMock()
        aggregate_response.content = [TextBlock(text="Fresh work complete.")]
        coordinator._provider.send_message = AsyncMock(side_effect=[
            synthesis_response, decompose_response, aggregate_response,
        ])

        # Member doesn't exist
        coordinator._manager.get_member = MagicMock(return_value=None)
        fake_member = MagicMock(id="new1", role="coder", task="fresh task")
        coordinator._manager.create_member = AsyncMock(return_value=fake_member)
        coordinator._manager.mailbox = MagicMock()
        coordinator._manager.mailbox.receive_and_clear = MagicMock(return_value=[
            MagicMock(text="DONE")
        ])
        coordinator.TIMEOUT = 1.0
        coordinator.POLL_INTERVAL = 0.05

        result = await coordinator.orchestrate("task", resume_member_ids=["nonexistent"])

        # Should have fallen through to fresh spawn
        coordinator._manager.create_member.assert_called_once()
        assert "Resumable swarm member IDs" in result

    @pytest.mark.asyncio
    async def test_orchestrate_includes_resumable_ids_in_output(self, coordinator):
        synthesis_response = MagicMock()
        synthesis_response.content = [TextBlock(text='{"should_delegate": true}')]
        decompose_response = MagicMock()
        decompose_response.content = [TextBlock(text='[{"role": "coder", "task": "x"}]')]
        aggregate_response = MagicMock()
        aggregate_response.content = [TextBlock(text="Done.")]
        coordinator._provider.send_message = AsyncMock(side_effect=[
            synthesis_response, decompose_response, aggregate_response,
        ])

        fake_member = MagicMock(id="abc123", role="coder", task="x")
        coordinator._manager.create_member = AsyncMock(return_value=fake_member)
        coordinator._manager.mailbox = MagicMock()
        coordinator._manager.mailbox.receive_and_clear = MagicMock(return_value=[
            MagicMock(text="DONE")
        ])
        coordinator.TIMEOUT = 1.0
        coordinator.POLL_INTERVAL = 0.05

        result = await coordinator.orchestrate("task")
        assert "abc123" in result
        assert "Resumable swarm member IDs" in result
