"""McpServerManager: lifecycle management for MCP server connections."""
from __future__ import annotations

import asyncio
import time
import warnings
from dataclasses import dataclass
from typing import Awaitable, Callable

from llm_code.logging import get_logger
from llm_code.mcp.agent_approval import AgentMCPRegistry, MCPApprovalRequest
from llm_code.mcp.bridge import McpToolBridge
from llm_code.mcp.client import McpClient
from llm_code.mcp.health import MCPHealthChecker
from llm_code.mcp.transport import HttpTransport, McpTransport, SseTransport, StdioTransport, WebSocketTransport
from llm_code.mcp.types import McpServerConfig
from llm_code.tools.registry import ToolRegistry

logger = get_logger(__name__)

# Exponential backoff: 5s, 10s, 20s, 40s → capped at 60s
_BACKOFF_BASE = 5.0
_BACKOFF_MAX = 60.0

ROOT_AGENT_ID = "root"

ApprovalCallback = Callable[[MCPApprovalRequest], Awaitable[bool]]


class MCPApprovalDeniedError(RuntimeError):
    """Raised when a user (or default-deny policy) rejects an MCP spawn request."""


@dataclass(frozen=True)
class ServerInstance:
    """Bookkeeping record for a running MCP server."""

    client: McpClient
    owner_agent_id: str
    started_at: float
    config: McpServerConfig


def _backoff_delay(attempt: int) -> float:
    """Return the delay in seconds for *attempt* (0-indexed), capped at _BACKOFF_MAX."""
    return min(_BACKOFF_BASE * (2 ** attempt), _BACKOFF_MAX)


class McpServerManager:
    """Manages the lifecycle of MCP server connections and tool registration."""

    def __init__(self) -> None:
        self._transports: dict[str, McpTransport] = {}
        self._clients: dict[str, McpClient] = {}
        self._instances: dict[str, ServerInstance] = {}
        self._configs: dict[str, McpServerConfig] = {}
        self._instructions: dict[str, str] = {}
        self._health: MCPHealthChecker = MCPHealthChecker()
        # Track consecutive reconnect failures for backoff
        self._reconnect_failures: dict[str, int] = {}
        # Per-agent ownership registry (Wave B/C Feature 8 integration)
        self._registry: AgentMCPRegistry = AgentMCPRegistry()

    @property
    def registry(self) -> AgentMCPRegistry:
        """Expose the agent-ownership registry (read-only use recommended)."""
        return self._registry

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    async def start_server(
        self,
        name: str,
        config: McpServerConfig,
        owner_agent_id: str = ROOT_AGENT_ID,
        approval_callback: ApprovalCallback | None = None,
    ) -> McpClient:
        """Start a single MCP server, returning an initialised McpClient.

        Non-root agents must pass ``approval_callback``; if the callback
        returns False the spawn is aborted with :class:`MCPApprovalDeniedError`
        and no state is mutated.  Root (default) always skips approval for
        backward compatibility.

        Callers that spawn MCP servers on behalf of personas, swarm
        members, or orchestrated sub-agents pass
        ``ConversationRuntime.request_mcp_approval`` as ``approval_callback``
        so the TUI surfaces a user prompt before the spawn proceeds. The TUI
        sink is wired automatically in :class:`LLMCodeApp._init_runtime`.
        See also ``orchestrate_executor.py`` for persona-driven spawns.
        """
        if owner_agent_id != ROOT_AGENT_ID and approval_callback is not None:
            request = MCPApprovalRequest(
                agent_id=owner_agent_id,
                agent_name=owner_agent_id,
                server_names=(name,),
                reason=f"spawn MCP server '{name}'",
            )
            approved = False
            try:
                approved = await approval_callback(request)
            except Exception as exc:  # noqa: BLE001
                logger.warning("MCP approval callback raised for %s: %s", name, exc)
                approved = False
            if not approved:
                raise MCPApprovalDeniedError(
                    f"User denied MCP server '{name}' for agent '{owner_agent_id}'"
                )

        logger.debug("Starting MCP server: %s (owner=%s)", name, owner_agent_id)
        transport = self._build_transport(config)
        await transport.start()
        client = McpClient(transport)
        info = await client.initialize()
        self._transports[name] = transport
        self._clients[name] = client
        self._instances[name] = ServerInstance(
            client=client,
            owner_agent_id=owner_agent_id,
            started_at=time.monotonic(),
            config=config,
        )
        self._registry.track_owner(owner_agent_id, (name,))
        self._configs[name] = config
        self._reconnect_failures[name] = 0

        # Extract server instructions from capabilities if present
        capabilities = info.capabilities or {}
        instructions = capabilities.get("instructions", "")
        if instructions:
            self._instructions[name] = instructions

        logger.debug("MCP server started: %s", name)
        return client

    async def start_all(self, configs: dict[str, McpServerConfig]) -> None:
        """Start all servers defined in *configs*, logging warnings on failure."""
        for name, config in configs.items():
            try:
                await self.start_server(name, config)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to start MCP server '%s': %s", name, exc)
                warnings.warn(
                    f"Failed to start MCP server '{name}': {exc}",
                    stacklevel=2,
                )

    async def stop_all(self) -> None:
        """Close ALL active clients and clear internal state (superuser op)."""
        logger.debug("Stopping all MCP servers (%d active)", len(self._clients))
        self._health.stop_monitor()
        for name, client in list(self._clients.items()):
            try:
                await client.close()
                logger.debug("MCP server stopped: %s", name)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error stopping MCP server '%s': %s", name, exc)
        self._clients.clear()
        self._transports.clear()
        self._instances.clear()
        # Drop all ownership records — parity with the full clear above.
        for agent_id in self._registry.all_agents():
            self._registry.cleanup_owned_servers(agent_id)

    async def cleanup_for_agent(self, agent_id: str) -> list[str]:
        """Stop only servers owned by *agent_id*.

        Returns the list of server names that were stopped. Root-owned
        servers are never touched by this call.
        """
        owned = list(self._registry.owned_by(agent_id))
        stopped: list[str] = []
        for name in owned:
            instance = self._instances.get(name)
            if instance is None:
                continue
            try:
                await instance.client.close()
                stopped.append(name)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to stop MCP server '%s': %s", name, exc)
            finally:
                self._instances.pop(name, None)
                self._clients.pop(name, None)
                self._transports.pop(name, None)
        # Drop ownership bookkeeping for the agent as a whole.
        self._registry.cleanup_owned_servers(agent_id)
        return stopped

    def get_client(self, name: str) -> McpClient | None:
        """Return the client for *name*, or None if not registered."""
        return self._clients.get(name)

    def get_instance(self, name: str) -> ServerInstance | None:
        """Return the full ServerInstance record for *name*, or None."""
        return self._instances.get(name)

    def get_all_instructions(self) -> dict[str, str]:
        """Return a mapping of server name → instructions for all servers that provided them."""
        return {k: v for k, v in self._instructions.items() if v}

    # ------------------------------------------------------------------
    # Health checking
    # ------------------------------------------------------------------

    @property
    def health(self) -> MCPHealthChecker:
        """Return the :class:`MCPHealthChecker` instance."""
        return self._health

    async def check_server_health(self, name: str) -> bool:
        """Check health of a single server.  Returns True if alive."""
        client = self._clients.get(name)
        if client is None:
            return False
        status = await self._health.check_server(name, client)
        return status.alive

    async def check_all_health(self):  # type: ignore[return]
        """Check health of all connected servers concurrently."""
        return await self._health.check_all(self._clients)

    def start_health_monitor(self, interval: float = 60.0) -> None:
        """Start background health monitoring for all current clients."""
        self._health.start_background_monitor(self._clients, interval=interval)

    async def ensure_healthy(self, name: str) -> McpClient:
        """Return a healthy client for *name*, attempting reconnection once on failure.

        Raises :class:`RuntimeError` if the server is not connected or cannot be
        reconnected.  Uses exponential backoff on repeated reconnect failures.
        """
        client = self._clients.get(name)
        if client is None:
            raise RuntimeError(f"MCP server '{name}' is not connected")

        # Quick health probe
        status = await self._health.check_server(name, client)
        if status.alive:
            self._reconnect_failures[name] = 0
            return client

        # Server appears unhealthy — attempt one reconnect
        logger.warning("MCP server '%s' is unhealthy (%s), attempting reconnect", name, status.error)
        config = self._configs.get(name)
        if config is None:
            raise RuntimeError(f"No config stored for MCP server '{name}' — cannot reconnect")

        failures = self._reconnect_failures.get(name, 0)
        if failures > 0:
            delay = _backoff_delay(failures - 1)
            logger.debug("Backoff %.0fs before reconnecting '%s' (attempt %d)", delay, name, failures + 1)
            await asyncio.sleep(delay)

        try:
            # Close stale connection
            try:
                await client.close()
            except Exception:  # noqa: BLE001
                pass

            new_client = await self.start_server(name, config)
            self._reconnect_failures[name] = 0
            logger.info("MCP server '%s' reconnected successfully", name)
            return new_client
        except Exception as exc:  # noqa: BLE001
            self._reconnect_failures[name] = failures + 1
            raise RuntimeError(
                f"Failed to reconnect MCP server '{name}': {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    async def register_all_tools(self, registry: ToolRegistry) -> int:
        """Discover and register MCP tools from all active servers.

        Returns the total number of tools registered.
        """
        total = 0
        for server_name, client in self._clients.items():
            tools = await client.list_tools()
            for mcp_tool in tools:
                bridge = McpToolBridge(server_name, mcp_tool, client)
                registry.register(bridge)
                total += 1
        return total

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_transport(config: McpServerConfig) -> McpTransport:
        """Build the appropriate transport from *config*.

        Supported ``transport_type`` values:
        - ``"stdio"`` (default) — subprocess stdin/stdout
        - ``"http"`` — HTTP POST requests
        - ``"sse"`` — Server-Sent Events over HTTP
        - ``"ws"`` / ``"websocket"`` — WebSocket (requires ``websockets`` package)
        """
        if config.transport_type == "http" and config.url:
            return HttpTransport(url=config.url, headers=config.headers)
        if config.transport_type == "sse" and config.url:
            return SseTransport(url=config.url, headers=config.headers)
        if config.transport_type in ("ws", "websocket") and config.url:
            return WebSocketTransport(url=config.url, headers=config.headers)
        if config.command:
            return StdioTransport(
                command=config.command,
                args=config.args,
                env=config.env,
            )
        raise ValueError(
            f"Cannot build transport from config: transport_type={config.transport_type!r}, "
            f"command={config.command!r}, url={config.url!r}"
        )
