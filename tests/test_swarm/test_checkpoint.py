"""Tests for agent team checkpoint system."""
from __future__ import annotations

import json

import pytest

from llm_code.swarm.checkpoint import (
    AgentCheckpoint,
    TeamCheckpoint,
    save_checkpoint,
    load_checkpoint,
    list_checkpoints,
)


class TestAgentCheckpoint:
    def test_create(self) -> None:
        cp = AgentCheckpoint(
            member_id="abc123",
            role="reviewer",
            status="running",
            conversation_snapshot=({"role": "user", "content": "hi"},),
        )
        assert cp.member_id == "abc123"
        assert cp.status == "running"
        assert len(cp.conversation_snapshot) == 1

    def test_defaults(self) -> None:
        cp = AgentCheckpoint(member_id="x", role="r", status="completed", conversation_snapshot=())
        assert cp.last_tool_call is None
        assert cp.output == ""


class TestTeamCheckpoint:
    def test_create(self) -> None:
        agent_cp = AgentCheckpoint(member_id="a", role="r", status="running", conversation_snapshot=())
        tcp = TeamCheckpoint(
            team_name="my-team",
            task_description="do stuff",
            timestamp="2026-04-05T12:00:00Z",
            checkpoints=(agent_cp,),
        )
        assert tcp.team_name == "my-team"
        assert len(tcp.checkpoints) == 1
        assert tcp.completed_members == ()


class TestCheckpointPersistence:
    def test_save_and_load(self, tmp_path) -> None:
        agent_cp = AgentCheckpoint(
            member_id="a1", role="coder", status="completed",
            conversation_snapshot=({"role": "assistant", "content": "done"},),
            output="result",
        )
        tcp = TeamCheckpoint(
            team_name="test", task_description="build feature",
            timestamp="2026-04-05T12:00:00Z",
            checkpoints=(agent_cp,), completed_members=("a1",),
        )
        path = save_checkpoint(tcp, tmp_path)
        assert path.exists()
        loaded = load_checkpoint(path)
        assert loaded.team_name == "test"
        assert loaded.task_description == "build feature"
        assert len(loaded.checkpoints) == 1
        assert loaded.checkpoints[0].output == "result"
        assert loaded.completed_members == ("a1",)

    def test_list_checkpoints_empty(self, tmp_path) -> None:
        assert list_checkpoints(tmp_path) == []

    def test_list_checkpoints(self, tmp_path) -> None:
        for i in range(3):
            tcp = TeamCheckpoint(
                team_name="t", task_description="d",
                timestamp=f"2026-04-05T12:0{i}:00Z", checkpoints=(),
            )
            save_checkpoint(tcp, tmp_path)
        result = list_checkpoints(tmp_path)
        assert len(result) == 3
