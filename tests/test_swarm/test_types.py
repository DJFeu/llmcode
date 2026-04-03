"""Tests for swarm types."""
from __future__ import annotations

import pytest

from llm_code.swarm.types import SwarmMember, SwarmMessage, SwarmStatus


class TestSwarmStatus:
    def test_has_expected_values(self):
        assert SwarmStatus.RUNNING.value == "running"
        assert SwarmStatus.STOPPED.value == "stopped"
        assert SwarmStatus.FAILED.value == "failed"
        assert SwarmStatus.STARTING.value == "starting"


class TestSwarmMember:
    def test_frozen(self):
        member = SwarmMember(
            id="abc123",
            role="researcher",
            task="Find security vulnerabilities",
            backend="subprocess",
            pid=12345,
            status=SwarmStatus.RUNNING,
        )
        with pytest.raises(AttributeError):
            member.status = SwarmStatus.STOPPED  # type: ignore[misc]

    def test_fields(self):
        member = SwarmMember(
            id="abc123",
            role="researcher",
            task="Find bugs",
            backend="tmux",
            pid=None,
            status=SwarmStatus.STARTING,
        )
        assert member.id == "abc123"
        assert member.role == "researcher"
        assert member.task == "Find bugs"
        assert member.backend == "tmux"
        assert member.pid is None
        assert member.status == SwarmStatus.STARTING

    def test_pid_optional(self):
        member = SwarmMember(
            id="x",
            role="coder",
            task="write code",
            backend="subprocess",
            pid=None,
            status=SwarmStatus.RUNNING,
        )
        assert member.pid is None


class TestSwarmMessage:
    def test_frozen(self):
        msg = SwarmMessage(
            from_id="a",
            to_id="b",
            text="hello",
            timestamp="2026-04-03T00:00:00Z",
        )
        with pytest.raises(AttributeError):
            msg.text = "changed"  # type: ignore[misc]

    def test_fields(self):
        msg = SwarmMessage(
            from_id="main",
            to_id="worker-1",
            text="Please analyze file.py",
            timestamp="2026-04-03T12:00:00Z",
        )
        assert msg.from_id == "main"
        assert msg.to_id == "worker-1"
        assert msg.text == "Please analyze file.py"
        assert msg.timestamp == "2026-04-03T12:00:00Z"

    def test_broadcast_to_id(self):
        """to_id='*' indicates a broadcast message."""
        msg = SwarmMessage(
            from_id="main",
            to_id="*",
            text="All stop",
            timestamp="2026-04-03T12:00:00Z",
        )
        assert msg.to_id == "*"
