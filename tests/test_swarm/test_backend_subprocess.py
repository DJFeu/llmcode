"""Tests for subprocess-based swarm backend."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from llm_code.swarm.backend_subprocess import SubprocessBackend


@pytest.fixture
def backend(tmp_path):
    return SubprocessBackend(swarm_dir=tmp_path / "swarm")


class TestSubprocessBackendSpawn:
    @pytest.mark.asyncio
    async def test_spawn_creates_log_dir(self, backend, tmp_path):
        mock_proc = AsyncMock()
        mock_proc.pid = 42
        mock_proc.returncode = None
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await backend.spawn(
                member_id="w1",
                role="coder",
                task="write tests",
            )
        log_dir = tmp_path / "swarm" / "w1"
        assert log_dir.exists()

    @pytest.mark.asyncio
    async def test_spawn_returns_pid(self, backend):
        mock_proc = AsyncMock()
        mock_proc.pid = 99
        mock_proc.returncode = None
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result_pid = await backend.spawn(
                member_id="w1",
                role="coder",
                task="write code",
            )
        assert result_pid == 99

    @pytest.mark.asyncio
    async def test_spawn_writes_output_log(self, backend, tmp_path):
        mock_proc = AsyncMock()
        mock_proc.pid = 1
        mock_proc.returncode = None
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await backend.spawn(member_id="w1", role="r", task="t")
        assert (tmp_path / "swarm" / "w1" / "output.log").exists()


class TestSubprocessBackendStop:
    @pytest.mark.asyncio
    async def test_stop_terminates_process(self, backend):
        mock_proc = AsyncMock()
        mock_proc.pid = 50
        mock_proc.returncode = None
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await backend.spawn(member_id="w1", role="r", task="t")
        await backend.stop("w1")
        mock_proc.terminate.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_unknown_id(self, backend):
        """Stopping an unknown member should not raise."""
        await backend.stop("nonexistent")


class TestSubprocessBackendIsRunning:
    @pytest.mark.asyncio
    async def test_is_running_true(self, backend):
        mock_proc = AsyncMock()
        mock_proc.pid = 10
        mock_proc.returncode = None
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await backend.spawn(member_id="w1", role="r", task="t")
        assert backend.is_running("w1") is True

    @pytest.mark.asyncio
    async def test_is_running_false_after_stop(self, backend):
        mock_proc = AsyncMock()
        mock_proc.pid = 10
        mock_proc.returncode = 0
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await backend.spawn(member_id="w1", role="r", task="t")
        mock_proc.returncode = 0
        assert backend.is_running("w1") is False
