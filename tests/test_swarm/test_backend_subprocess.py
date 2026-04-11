"""Tests for subprocess-based swarm backend."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_code.swarm.backend_subprocess import SubprocessBackend


def _make_mock_proc(pid: int = 42, returncode=None) -> MagicMock:
    """Build a mock ``asyncio.subprocess.Process`` with the right sync/async mix.

    ``asyncio.subprocess.Process`` has a mixed API — some methods are
    sync, others are coroutines — and its ``stdin`` (a ``StreamWriter``)
    mixes them too. Using a bare ``AsyncMock()`` for the whole process
    makes every method return a coroutine, so the production sync calls
    (``stdin.write()``, ``terminate()``) generate never-awaited coroutines
    and Python raises ``RuntimeWarning``.

    This helper returns a ``MagicMock`` (sync by default) with the async
    methods explicitly re-wrapped as ``AsyncMock``, matching the real
    surface:

    * sync: ``pid``, ``returncode``, ``terminate()``, ``kill()``,
      ``send_signal()``, ``stdin.write()``, ``stdin.writelines()``,
      ``stdin.close()``
    * async: ``wait()``, ``communicate()``, ``stdin.drain()``,
      ``stdin.wait_closed()``
    """
    proc = MagicMock()
    proc.pid = pid
    proc.returncode = returncode
    # Async methods on Process
    proc.wait = AsyncMock(return_value=returncode)
    proc.communicate = AsyncMock(return_value=(b"", b""))
    # stdin: sync write() + writelines() + close(), async drain() + wait_closed()
    proc.stdin = MagicMock()
    proc.stdin.drain = AsyncMock()
    proc.stdin.wait_closed = AsyncMock()
    return proc


@pytest.fixture
def backend(tmp_path):
    return SubprocessBackend(swarm_dir=tmp_path / "swarm")


class TestSubprocessBackendSpawn:
    @pytest.mark.asyncio
    async def test_spawn_creates_log_dir(self, backend, tmp_path):
        mock_proc = _make_mock_proc(pid=42)
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
        mock_proc = _make_mock_proc(pid=99)
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result_pid = await backend.spawn(
                member_id="w1",
                role="coder",
                task="write code",
            )
        assert result_pid == 99

    @pytest.mark.asyncio
    async def test_spawn_writes_output_log(self, backend, tmp_path):
        mock_proc = _make_mock_proc(pid=1)
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await backend.spawn(member_id="w1", role="r", task="t")
        assert (tmp_path / "swarm" / "w1" / "output.log").exists()


class TestSubprocessBackendStop:
    @pytest.mark.asyncio
    async def test_stop_terminates_process(self, backend):
        mock_proc = _make_mock_proc(pid=50)
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
        mock_proc = _make_mock_proc(pid=10)
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await backend.spawn(member_id="w1", role="r", task="t")
        assert backend.is_running("w1") is True

    @pytest.mark.asyncio
    async def test_is_running_false_after_stop(self, backend):
        mock_proc = _make_mock_proc(pid=10, returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await backend.spawn(member_id="w1", role="r", task="t")
        assert backend.is_running("w1") is False
