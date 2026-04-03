"""MCP server health checking and background monitoring."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from llm_code.logging import get_logger

logger = get_logger(__name__)

_HEALTH_CHECK_TIMEOUT = 5.0  # seconds


@dataclass(frozen=True)
class HealthStatus:
    """Health status snapshot for a single MCP server."""

    name: str
    alive: bool
    latency_ms: float
    error: str | None
    last_checked: float


class MCPHealthChecker:
    """Check and monitor the health of MCP server connections."""

    def __init__(self) -> None:
        self._monitor_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._latest: dict[str, HealthStatus] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def check_server(self, name: str, client) -> HealthStatus:  # type: ignore[type-arg]
        """Probe *client* with a listTools call (5 s timeout).

        Returns a :class:`HealthStatus` reflecting whether the server is alive.
        """
        start = time.monotonic()
        try:
            await asyncio.wait_for(client.list_tools(), timeout=_HEALTH_CHECK_TIMEOUT)
            latency_ms = (time.monotonic() - start) * 1000
            status = HealthStatus(
                name=name,
                alive=True,
                latency_ms=latency_ms,
                error=None,
                last_checked=time.time(),
            )
        except asyncio.TimeoutError:
            latency_ms = (time.monotonic() - start) * 1000
            status = HealthStatus(
                name=name,
                alive=False,
                latency_ms=latency_ms,
                error="timeout",
                last_checked=time.time(),
            )
            logger.warning("MCP server '%s' health check timed out", name)
        except Exception as exc:  # noqa: BLE001
            latency_ms = (time.monotonic() - start) * 1000
            status = HealthStatus(
                name=name,
                alive=False,
                latency_ms=latency_ms,
                error=str(exc),
                last_checked=time.time(),
            )
            logger.warning("MCP server '%s' health check failed: %s", name, exc)

        self._latest[name] = status
        return status

    async def check_all(self, servers: dict) -> list[HealthStatus]:  # type: ignore[type-arg]
        """Check all servers concurrently.

        *servers* maps server name → client.
        """
        if not servers:
            return []
        tasks = [self.check_server(name, client) for name, client in servers.items()]
        return list(await asyncio.gather(*tasks))

    def start_background_monitor(self, servers: dict, interval: float = 60.0) -> None:  # type: ignore[type-arg]
        """Start an asyncio background task that polls *servers* every *interval* seconds."""
        if self._monitor_task is not None and not self._monitor_task.done():
            return

        async def _monitor() -> None:
            while True:
                previous = {name: s.alive for name, s in self._latest.items()}
                await self.check_all(servers)
                for name, status in self._latest.items():
                    was_alive = previous.get(name)
                    if was_alive is None:
                        continue
                    if was_alive and not status.alive:
                        logger.warning("MCP server '%s' became unhealthy: %s", name, status.error)
                    elif not was_alive and status.alive:
                        logger.info("MCP server '%s' recovered", name)
                await asyncio.sleep(interval)

        self._monitor_task = asyncio.ensure_future(_monitor())

    def stop_monitor(self) -> None:
        """Cancel the background monitoring task if running."""
        if self._monitor_task is not None and not self._monitor_task.done():
            self._monitor_task.cancel()
        self._monitor_task = None

    def get_status(self, name: str) -> HealthStatus | None:
        """Return the most recent :class:`HealthStatus` for *name*, or ``None``."""
        return self._latest.get(name)

    def get_all_statuses(self) -> dict[str, HealthStatus]:
        """Return all known health statuses."""
        return dict(self._latest)
