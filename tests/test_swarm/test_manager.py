"""Tests for SwarmManager orchestration."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from llm_code.swarm.manager import SwarmManager
from llm_code.swarm.types import SwarmStatus


@pytest.fixture
def manager(tmp_path):
    return SwarmManager(
        swarm_dir=tmp_path / "swarm",
        max_members=3,
        backend_preference="subprocess",
    )


class TestCreateMember:
    @pytest.mark.asyncio
    async def test_create_returns_member(self, manager):
        with patch.object(manager, "_subprocess_backend") as mock_be:
            mock_be.spawn = AsyncMock(return_value=123)
            member = await manager.create_member(role="coder", task="write tests")
        assert member.role == "coder"
        assert member.task == "write tests"
        assert member.status == SwarmStatus.RUNNING
        assert member.backend == "subprocess"

    @pytest.mark.asyncio
    async def test_create_assigns_unique_id(self, manager):
        with patch.object(manager, "_subprocess_backend") as mock_be:
            mock_be.spawn = AsyncMock(return_value=1)
            m1 = await manager.create_member(role="a", task="t")
            mock_be.spawn = AsyncMock(return_value=2)
            m2 = await manager.create_member(role="b", task="t")
        assert m1.id != m2.id

    @pytest.mark.asyncio
    async def test_create_respects_max_members(self, manager):
        with patch.object(manager, "_subprocess_backend") as mock_be:
            mock_be.spawn = AsyncMock(return_value=1)
            for i in range(3):
                await manager.create_member(role=f"r{i}", task="t")
            with pytest.raises(ValueError, match="max"):
                await manager.create_member(role="extra", task="t")

    @pytest.mark.asyncio
    async def test_create_auto_detects_tmux(self, tmp_path):
        mgr = SwarmManager(
            swarm_dir=tmp_path / "swarm",
            max_members=5,
            backend_preference="auto",
        )
        with patch("llm_code.swarm.manager.is_tmux_available", return_value=False):
            with patch.object(mgr, "_subprocess_backend") as mock_be:
                mock_be.spawn = AsyncMock(return_value=1)
                member = await mgr.create_member(role="r", task="t")
        assert member.backend == "subprocess"


class TestListMembers:
    @pytest.mark.asyncio
    async def test_list_empty(self, manager):
        assert manager.list_members() == []

    @pytest.mark.asyncio
    async def test_list_returns_all(self, manager):
        with patch.object(manager, "_subprocess_backend") as mock_be:
            mock_be.spawn = AsyncMock(return_value=1)
            await manager.create_member(role="a", task="t1")
            mock_be.spawn = AsyncMock(return_value=2)
            await manager.create_member(role="b", task="t2")
        assert len(manager.list_members()) == 2


class TestStopMember:
    @pytest.mark.asyncio
    async def test_stop_removes_from_list(self, manager):
        with patch.object(manager, "_subprocess_backend") as mock_be:
            mock_be.spawn = AsyncMock(return_value=1)
            mock_be.stop = AsyncMock()
            member = await manager.create_member(role="r", task="t")
            await manager.stop_member(member.id)
        assert len(manager.list_members()) == 0

    @pytest.mark.asyncio
    async def test_stop_unknown_raises(self, manager):
        with pytest.raises(KeyError):
            await manager.stop_member("nonexistent")


class TestStopAll:
    @pytest.mark.asyncio
    async def test_stop_all_clears_list(self, manager):
        with patch.object(manager, "_subprocess_backend") as mock_be:
            mock_be.spawn = AsyncMock(return_value=1)
            mock_be.stop = AsyncMock()
            mock_be.stop_all = AsyncMock()
            await manager.create_member(role="a", task="t")
            await manager.create_member(role="b", task="t")
            await manager.stop_all()
        assert manager.list_members() == []
