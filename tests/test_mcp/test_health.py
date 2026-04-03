"""Tests for MCPHealthChecker (health.py)."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_code.mcp.health import HealthStatus, MCPHealthChecker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(tools: list | None = None, *, raise_exc: Exception | None = None):
    """Return a mock McpClient where list_tools behaves as specified."""
    client = MagicMock()
    if raise_exc is not None:
        client.list_tools = AsyncMock(side_effect=raise_exc)
    else:
        client.list_tools = AsyncMock(return_value=tools or [])
    return client


# ---------------------------------------------------------------------------
# HealthStatus dataclass
# ---------------------------------------------------------------------------

class TestHealthStatus:
    def test_is_frozen(self):
        status = HealthStatus(
            name="srv", alive=True, latency_ms=10.0, error=None, last_checked=time.time()
        )
        with pytest.raises((AttributeError, TypeError)):
            status.alive = False  # type: ignore[misc]

    def test_fields_accessible(self):
        ts = time.time()
        status = HealthStatus(name="x", alive=False, latency_ms=99.9, error="oops", last_checked=ts)
        assert status.name == "x"
        assert status.alive is False
        assert status.latency_ms == pytest.approx(99.9)
        assert status.error == "oops"
        assert status.last_checked == ts


# ---------------------------------------------------------------------------
# check_server
# ---------------------------------------------------------------------------

class TestCheckServer:
    @pytest.mark.asyncio
    async def test_healthy_server_returns_alive_true(self):
        client = _make_client(tools=[])
        checker = MCPHealthChecker()
        status = await checker.check_server("my-srv", client)

        assert isinstance(status, HealthStatus)
        assert status.name == "my-srv"
        assert status.alive is True
        assert status.error is None
        assert status.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_healthy_server_stores_status(self):
        client = _make_client(tools=[])
        checker = MCPHealthChecker()
        await checker.check_server("store-test", client)
        assert checker.get_status("store-test") is not None
        assert checker.get_status("store-test").alive is True  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_timeout_returns_alive_false(self):
        async def slow_list_tools():
            await asyncio.sleep(10)  # will be cancelled by timeout

        client = MagicMock()
        client.list_tools = slow_list_tools

        checker = MCPHealthChecker()
        # Patch timeout to 0.01s so the test is fast
        with patch("llm_code.mcp.health._HEALTH_CHECK_TIMEOUT", 0.01):
            status = await checker.check_server("slow-srv", client)

        assert status.alive is False
        assert status.error == "timeout"

    @pytest.mark.asyncio
    async def test_exception_returns_alive_false(self):
        client = _make_client(raise_exc=RuntimeError("connection refused"))
        checker = MCPHealthChecker()
        status = await checker.check_server("broken-srv", client)

        assert status.alive is False
        assert "connection refused" in (status.error or "")

    @pytest.mark.asyncio
    async def test_last_checked_is_recent(self):
        client = _make_client()
        checker = MCPHealthChecker()
        before = time.time()
        status = await checker.check_server("ts-srv", client)
        after = time.time()

        assert before <= status.last_checked <= after


# ---------------------------------------------------------------------------
# check_all
# ---------------------------------------------------------------------------

class TestCheckAll:
    @pytest.mark.asyncio
    async def test_empty_servers_returns_empty_list(self):
        checker = MCPHealthChecker()
        result = await checker.check_all({})
        assert result == []

    @pytest.mark.asyncio
    async def test_checks_all_servers_concurrently(self):
        clients = {
            "alpha": _make_client(),
            "beta": _make_client(),
            "gamma": _make_client(raise_exc=RuntimeError("down")),
        }
        checker = MCPHealthChecker()
        statuses = await checker.check_all(clients)

        assert len(statuses) == 3
        names = {s.name for s in statuses}
        assert names == {"alpha", "beta", "gamma"}

        by_name = {s.name: s for s in statuses}
        assert by_name["alpha"].alive is True
        assert by_name["beta"].alive is True
        assert by_name["gamma"].alive is False

    @pytest.mark.asyncio
    async def test_get_all_statuses_after_check_all(self):
        clients = {"srv1": _make_client(), "srv2": _make_client()}
        checker = MCPHealthChecker()
        await checker.check_all(clients)

        all_statuses = checker.get_all_statuses()
        assert set(all_statuses.keys()) == {"srv1", "srv2"}


# ---------------------------------------------------------------------------
# Background monitor start/stop
# ---------------------------------------------------------------------------

class TestBackgroundMonitor:
    @pytest.mark.asyncio
    async def test_start_creates_background_task(self):
        checker = MCPHealthChecker()
        clients = {"srv": _make_client()}

        checker.start_background_monitor(clients, interval=1000)
        assert checker._monitor_task is not None
        assert not checker._monitor_task.done()

        checker.stop_monitor()

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self):
        checker = MCPHealthChecker()
        clients = {"srv": _make_client()}

        checker.start_background_monitor(clients, interval=1000)
        task = checker._monitor_task
        checker.stop_monitor()

        # Give the event loop a tick to process the cancellation
        await asyncio.sleep(0)
        assert checker._monitor_task is None
        assert task is not None and task.cancelled()

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self):
        """Calling start_background_monitor twice should not create a second task."""
        checker = MCPHealthChecker()
        clients = {"srv": _make_client()}

        checker.start_background_monitor(clients, interval=1000)
        task1 = checker._monitor_task
        checker.start_background_monitor(clients, interval=1000)
        task2 = checker._monitor_task

        assert task1 is task2

        checker.stop_monitor()

    @pytest.mark.asyncio
    async def test_monitor_polls_servers(self):
        """Monitor should call list_tools at least once during a short interval."""
        client = _make_client()
        clients = {"poll-srv": client}

        checker = MCPHealthChecker()
        # Use a very short interval so first poll happens fast
        checker.start_background_monitor(clients, interval=0.05)

        # Allow a couple of event loop iterations for the initial poll
        await asyncio.sleep(0.1)
        checker.stop_monitor()

        assert client.list_tools.call_count >= 1

    @pytest.mark.asyncio
    async def test_stop_when_no_monitor_is_safe(self):
        """stop_monitor should not raise when called without a running task."""
        checker = MCPHealthChecker()
        checker.stop_monitor()  # Should not raise


# ---------------------------------------------------------------------------
# Reconnection via McpServerManager.ensure_healthy
# ---------------------------------------------------------------------------

class TestEnsureHealthy:
    @pytest.mark.asyncio
    async def test_healthy_server_returns_client(self):
        from llm_code.mcp.manager import McpServerManager

        manager = McpServerManager()
        client = _make_client()
        manager._clients["ok-srv"] = client

        result = await manager.ensure_healthy("ok-srv")
        assert result is client

    @pytest.mark.asyncio
    async def test_unknown_server_raises(self):
        from llm_code.mcp.manager import McpServerManager

        manager = McpServerManager()
        with pytest.raises(RuntimeError, match="not connected"):
            await manager.ensure_healthy("ghost")

    @pytest.mark.asyncio
    async def test_unhealthy_server_attempts_reconnect(self):
        from llm_code.mcp.manager import McpServerManager
        from llm_code.mcp.types import McpServerConfig

        # First call to list_tools fails (unhealthy), then reconnect succeeds
        broken_client = _make_client(raise_exc=RuntimeError("timeout"))
        broken_client.close = AsyncMock()

        config = McpServerConfig(command="fake-cmd")
        healthy_client = _make_client()

        manager = McpServerManager()
        manager._clients["reconnect-srv"] = broken_client
        manager._configs["reconnect-srv"] = config
        manager._reconnect_failures["reconnect-srv"] = 0

        # Patch start_server to return our healthy_client without real transport
        manager.start_server = AsyncMock(return_value=healthy_client)  # type: ignore[method-assign]

        result = await manager.ensure_healthy("reconnect-srv")

        assert result is healthy_client
        manager.start_server.assert_awaited_once_with("reconnect-srv", config)

    @pytest.mark.asyncio
    async def test_failed_reconnect_increments_failure_counter(self):
        from llm_code.mcp.manager import McpServerManager
        from llm_code.mcp.types import McpServerConfig

        broken_client = _make_client(raise_exc=RuntimeError("down"))
        broken_client.close = AsyncMock()

        config = McpServerConfig(command="fake-cmd")
        manager = McpServerManager()
        manager._clients["fail-srv"] = broken_client
        manager._configs["fail-srv"] = config
        manager._reconnect_failures["fail-srv"] = 0

        manager.start_server = AsyncMock(side_effect=RuntimeError("reconnect also failed"))  # type: ignore[method-assign]

        with pytest.raises(RuntimeError, match="Failed to reconnect"):
            await manager.ensure_healthy("fail-srv")

        assert manager._reconnect_failures["fail-srv"] == 1

    @pytest.mark.asyncio
    async def test_backoff_delay_grows_with_failures(self):
        from llm_code.mcp.manager import _backoff_delay

        assert _backoff_delay(0) == 5.0
        assert _backoff_delay(1) == 10.0
        assert _backoff_delay(2) == 20.0
        assert _backoff_delay(3) == 40.0
        assert _backoff_delay(4) == 60.0   # capped
        assert _backoff_delay(10) == 60.0  # still capped
