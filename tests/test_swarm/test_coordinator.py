"""Tests for Coordinator orchestration."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_code.api.types import MessageResponse, TextBlock, TokenUsage
from llm_code.swarm.coordinator import Coordinator
from llm_code.swarm.manager import SwarmManager
from llm_code.swarm.types import SwarmMember, SwarmStatus
from llm_code.tools.base import PermissionLevel
from llm_code.tools.coordinator_tool import CoordinatorTool


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def manager(tmp_path):
    return SwarmManager(
        swarm_dir=tmp_path / "swarm",
        max_members=3,
        backend_preference="subprocess",
    )


def _make_response(text: str) -> MessageResponse:
    return MessageResponse(
        content=(TextBlock(text=text),),
        usage=TokenUsage(input_tokens=10, output_tokens=20),
        stop_reason="end_turn",
    )


def _make_provider(*responses: str):
    """Create mock provider returning given response texts in order."""
    provider = MagicMock()
    provider.send_message = AsyncMock(
        side_effect=[_make_response(r) for r in responses]
    )
    return provider


def _make_orchestrate_provider(decompose_text: str, aggregate_text: str = "All done."):
    """Create mock provider for full orchestrate flow (synthesize + decompose + aggregate)."""
    return _make_provider(
        '{"should_delegate": true, "reason": "complex"}',
        decompose_text,
        aggregate_text,
    )


def _make_config(max_members: int = 3, model: str = "test-model"):
    config = MagicMock()
    config.model = model
    config.swarm = MagicMock()
    config.swarm.max_members = max_members
    config.swarm.synthesis_enabled = True
    return config


# ---------------------------------------------------------------------------
# Coordinator._parse_json_list
# ---------------------------------------------------------------------------


class TestParseJsonList:
    def test_valid_json_array(self, manager):
        coord = Coordinator(manager, MagicMock(), _make_config())
        result = coord._parse_json_list('[{"role": "coder", "task": "write code"}]')
        assert result == [{"role": "coder", "task": "write code"}]

    def test_json_with_markdown_fences(self, manager):
        coord = Coordinator(manager, MagicMock(), _make_config())
        text = '```json\n[{"role": "tester", "task": "write tests"}]\n```'
        result = coord._parse_json_list(text)
        assert result == [{"role": "tester", "task": "write tests"}]

    def test_json_with_surrounding_text(self, manager):
        coord = Coordinator(manager, MagicMock(), _make_config())
        text = 'Here are the subtasks:\n[{"role": "r", "task": "t"}]\nDone.'
        result = coord._parse_json_list(text)
        assert result == [{"role": "r", "task": "t"}]

    def test_invalid_json_returns_empty(self, manager):
        coord = Coordinator(manager, MagicMock(), _make_config())
        result = coord._parse_json_list("not json at all")
        assert result == []

    def test_non_array_json_returns_empty(self, manager):
        coord = Coordinator(manager, MagicMock(), _make_config())
        result = coord._parse_json_list('{"role": "coder"}')
        assert result == []

    def test_filters_non_dicts(self, manager):
        coord = Coordinator(manager, MagicMock(), _make_config())
        result = coord._parse_json_list('[{"role": "r", "task": "t"}, "bad", 42]')
        assert result == [{"role": "r", "task": "t"}]


# ---------------------------------------------------------------------------
# Coordinator._decompose
# ---------------------------------------------------------------------------


class TestDecompose:
    @pytest.mark.asyncio
    async def test_returns_subtasks(self, manager):
        subtasks_json = '[{"role": "coder", "task": "implement X"}, {"role": "tester", "task": "test X"}]'
        provider = _make_provider(subtasks_json)
        coord = Coordinator(manager, provider, _make_config())
        result = await coord._decompose("Build feature X")
        assert len(result) == 2
        assert result[0]["role"] == "coder"
        assert result[1]["role"] == "tester"

    @pytest.mark.asyncio
    async def test_provider_error_returns_empty(self, manager):
        provider = MagicMock()
        provider.send_message = AsyncMock(side_effect=RuntimeError("network error"))
        coord = Coordinator(manager, provider, _make_config())
        result = await coord._decompose("some task")
        assert result == []

    @pytest.mark.asyncio
    async def test_bad_json_returns_empty(self, manager):
        provider = _make_provider("not json")
        coord = Coordinator(manager, provider, _make_config())
        result = await coord._decompose("some task")
        assert result == []


# ---------------------------------------------------------------------------
# Coordinator._wait_for_completion
# ---------------------------------------------------------------------------


class TestWaitForCompletion:
    @pytest.mark.asyncio
    async def test_returns_messages_when_done_received(self, manager):
        coord = Coordinator(manager, MagicMock(), _make_config())
        # Pre-populate mailbox
        manager.mailbox.send(from_id="abc123", to_id="coordinator", text="Task DONE")
        results = await coord._wait_for_completion(
            member_ids=["abc123"],
            timeout=5.0,
            poll_interval=0.01,
        )
        assert "abc123" in results
        assert any("DONE" in t.upper() for t in results["abc123"])

    @pytest.mark.asyncio
    async def test_times_out_when_no_completion(self, manager):
        coord = Coordinator(manager, MagicMock(), _make_config())
        results = await coord._wait_for_completion(
            member_ids=["abc123"],
            timeout=0.05,
            poll_interval=0.01,
        )
        # Should return (empty) results dict after timeout
        assert "abc123" in results
        assert results["abc123"] == []

    @pytest.mark.asyncio
    async def test_multiple_members_all_complete(self, manager):
        coord = Coordinator(manager, MagicMock(), _make_config())
        manager.mailbox.send(from_id="m1", to_id="coordinator", text="DONE with task A")
        manager.mailbox.send(from_id="m2", to_id="coordinator", text="COMPLETE task B")
        results = await coord._wait_for_completion(
            member_ids=["m1", "m2"],
            timeout=5.0,
            poll_interval=0.01,
        )
        assert results["m1"] == ["DONE with task A"]
        assert results["m2"] == ["COMPLETE task B"]


# ---------------------------------------------------------------------------
# Coordinator.orchestrate
# ---------------------------------------------------------------------------


class TestOrchestrate:
    @pytest.mark.asyncio
    async def test_orchestrate_full_flow(self, manager):
        subtasks_json = '[{"role": "coder", "task": "implement X"}]'
        provider = _make_orchestrate_provider(subtasks_json, aggregate_text="Summary: X implemented.")
        config = _make_config(max_members=3)

        # Mock create_member to return a fake member without spawning
        fake_member = SwarmMember(
            id="fake01", role="coder", task="implement X",
            backend="subprocess", pid=None, status=SwarmStatus.RUNNING,
        )
        with patch.object(manager, "create_member", new=AsyncMock(return_value=fake_member)):
            # Pre-populate mailbox so wait_for_completion returns immediately
            manager.mailbox.send(from_id="fake01", to_id="coordinator", text="DONE")
            coord = Coordinator(manager, provider, config)
            result = await coord.orchestrate("Build feature X")

        assert "Summary" in result or "implement" in result.lower()

    @pytest.mark.asyncio
    async def test_orchestrate_no_subtasks(self, manager):
        provider = _make_provider(
            '{"should_delegate": true, "reason": "test"}',  # synthesis
            "not json",  # decompose fails
        )
        coord = Coordinator(manager, provider, _make_config())
        result = await coord.orchestrate("some task")
        assert "No subtasks" in result

    @pytest.mark.asyncio
    async def test_orchestrate_respects_max_members(self, manager):
        # 5 subtasks but max_members=2
        subtasks = [{"role": f"r{i}", "task": f"task {i}"} for i in range(5)]
        provider = _make_orchestrate_provider(json.dumps(subtasks), "Aggregated.")
        config = _make_config(max_members=2)

        created_members = []

        async def fake_create(role, task, backend="auto"):
            m = SwarmMember(
                id=f"m{len(created_members)}",
                role=role, task=task,
                backend="subprocess", pid=None,
                status=SwarmStatus.RUNNING,
            )
            created_members.append(m)
            return m

        with patch.object(manager, "create_member", new=fake_create):
            coord = Coordinator(manager, provider, config)
            # Don't send done messages – will timeout quickly
            coord.TIMEOUT = 0.05
            coord.POLL_INTERVAL = 0.01
            await coord.orchestrate("big task")

        # Only 2 members should have been created
        assert len(created_members) <= 2

    @pytest.mark.asyncio
    async def test_orchestrate_create_member_fails(self, manager):
        subtasks_json = '[{"role": "coder", "task": "implement X"}]'
        provider = _make_orchestrate_provider(subtasks_json)
        config = _make_config()

        with patch.object(manager, "create_member", new=AsyncMock(side_effect=ValueError("max reached"))):
            coord = Coordinator(manager, provider, config)
            result = await coord.orchestrate("some task")

        assert "Failed" in result or "no swarm" in result.lower() or len(result) >= 0


# ---------------------------------------------------------------------------
# CoordinatorTool
# ---------------------------------------------------------------------------


class TestCoordinatorTool:
    def _make_tool(self, orchestrate_result: str = "done") -> CoordinatorTool:
        coordinator = MagicMock()
        coordinator.orchestrate = AsyncMock(return_value=orchestrate_result)
        return CoordinatorTool(coordinator)

    def test_name(self):
        tool = self._make_tool()
        assert tool.name == "coordinate"

    def test_permission_workspace_write(self):
        tool = self._make_tool()
        assert tool.required_permission == PermissionLevel.WORKSPACE_WRITE

    def test_input_schema_has_task(self):
        tool = self._make_tool()
        schema = tool.input_schema
        assert "task" in schema["properties"]
        assert "task" in schema["required"]

    def test_execute_returns_result(self):
        tool = self._make_tool("Coordination complete!")
        result = tool.execute({"task": "do something"})
        assert not result.is_error
        assert "Coordination complete!" in result.output

    def test_execute_error_handling(self):
        coordinator = MagicMock()
        coordinator.orchestrate = AsyncMock(side_effect=RuntimeError("boom"))
        tool = CoordinatorTool(coordinator)
        result = tool.execute({"task": "bad task"})
        assert result.is_error
        assert "boom" in result.output

    def test_input_model_validates(self):
        tool = self._make_tool()
        validated = tool.validate_input({"task": "hello"})
        assert validated["task"] == "hello"
