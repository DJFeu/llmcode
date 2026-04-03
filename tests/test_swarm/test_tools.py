"""Tests for swarm tools."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from llm_code.swarm.manager import SwarmManager
from llm_code.swarm.types import SwarmMember, SwarmStatus
from llm_code.tools.base import PermissionLevel
from llm_code.tools.swarm_create import SwarmCreateTool
from llm_code.tools.swarm_delete import SwarmDeleteTool
from llm_code.tools.swarm_list import SwarmListTool
from llm_code.tools.swarm_message import SwarmMessageTool


@pytest.fixture
def manager(tmp_path):
    return SwarmManager(
        swarm_dir=tmp_path / "swarm",
        max_members=5,
        backend_preference="subprocess",
    )


class TestSwarmCreateTool:
    def test_name(self, manager):
        tool = SwarmCreateTool(manager)
        assert tool.name == "swarm_create"

    def test_permission_full_access(self, manager):
        tool = SwarmCreateTool(manager)
        assert tool.required_permission == PermissionLevel.FULL_ACCESS

    def test_execute_calls_create_member(self, manager):
        tool = SwarmCreateTool(manager)
        member = SwarmMember(
            id="abc", role="coder", task="code", backend="subprocess",
            pid=1, status=SwarmStatus.RUNNING,
        )
        with patch.object(manager, "create_member", new_callable=AsyncMock, return_value=member):
            result = tool.execute({"role": "coder", "task": "code"})
        assert not result.is_error
        assert "abc" in result.output


class TestSwarmListTool:
    def test_name(self, manager):
        tool = SwarmListTool(manager)
        assert tool.name == "swarm_list"

    def test_permission_read_only(self, manager):
        tool = SwarmListTool(manager)
        assert tool.required_permission == PermissionLevel.READ_ONLY

    def test_execute_empty(self, manager):
        tool = SwarmListTool(manager)
        result = tool.execute({})
        assert "No swarm members" in result.output

    def test_execute_with_members(self, manager):
        tool = SwarmListTool(manager)
        member = SwarmMember(
            id="x1", role="reviewer", task="review", backend="subprocess",
            pid=42, status=SwarmStatus.RUNNING,
        )
        manager._members["x1"] = member
        result = tool.execute({})
        assert "x1" in result.output
        assert "reviewer" in result.output


class TestSwarmMessageTool:
    def test_name(self, manager):
        tool = SwarmMessageTool(manager)
        assert tool.name == "swarm_message"

    def test_permission_read_only(self, manager):
        tool = SwarmMessageTool(manager)
        assert tool.required_permission == PermissionLevel.READ_ONLY

    def test_execute_send(self, manager):
        tool = SwarmMessageTool(manager)
        result = tool.execute({
            "action": "send",
            "from_id": "main",
            "to_id": "w1",
            "text": "hello",
        })
        assert not result.is_error

    def test_execute_receive(self, manager):
        tool = SwarmMessageTool(manager)
        manager.mailbox.send("main", "w1", "hello")
        result = tool.execute({
            "action": "receive",
            "from_id": "main",
            "to_id": "w1",
        })
        assert "hello" in result.output


class TestSwarmDeleteTool:
    def test_name(self, manager):
        tool = SwarmDeleteTool(manager)
        assert tool.name == "swarm_delete"

    def test_permission_full_access(self, manager):
        tool = SwarmDeleteTool(manager)
        assert tool.required_permission == PermissionLevel.FULL_ACCESS

    def test_execute_stop_all(self, manager):
        tool = SwarmDeleteTool(manager)
        with patch.object(manager, "stop_all", new_callable=AsyncMock):
            result = tool.execute({"action": "stop_all"})
        assert not result.is_error

    def test_execute_stop_one(self, manager):
        tool = SwarmDeleteTool(manager)
        member = SwarmMember(
            id="x1", role="r", task="t", backend="subprocess",
            pid=1, status=SwarmStatus.RUNNING,
        )
        manager._members["x1"] = member
        with patch.object(manager, "stop_member", new_callable=AsyncMock):
            result = tool.execute({"action": "stop", "member_id": "x1"})
        assert not result.is_error
