"""McpServerManager: lifecycle management for MCP server connections."""
from __future__ import annotations

import warnings

from llm_code.logging import get_logger
from llm_code.mcp.bridge import McpToolBridge
from llm_code.mcp.client import McpClient
from llm_code.mcp.transport import HttpTransport, McpTransport, StdioTransport
from llm_code.mcp.types import McpServerConfig
from llm_code.tools.registry import ToolRegistry

logger = get_logger(__name__)


class McpServerManager:
    """Manages the lifecycle of MCP server connections and tool registration."""

    def __init__(self) -> None:
        self._transports: dict[str, McpTransport] = {}
        self._clients: dict[str, McpClient] = {}
        self._instructions: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    async def start_server(self, name: str, config: McpServerConfig) -> McpClient:
        """Start a single MCP server, returning an initialised McpClient."""
        logger.debug("Starting MCP server: %s", name)
        transport = self._build_transport(config)
        await transport.start()
        client = McpClient(transport)
        info = await client.initialize()
        self._transports[name] = transport
        self._clients[name] = client

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
        """Close all active clients and clear internal state."""
        logger.debug("Stopping all MCP servers (%d active)", len(self._clients))
        for name, client in list(self._clients.items()):
            try:
                await client.close()
                logger.debug("MCP server stopped: %s", name)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error stopping MCP server '%s': %s", name, exc)
        self._clients.clear()
        self._transports.clear()

    def get_client(self, name: str) -> McpClient | None:
        """Return the client for *name*, or None if not registered."""
        return self._clients.get(name)

    def get_all_instructions(self) -> dict[str, str]:
        """Return a mapping of server name → instructions for all servers that provided them."""
        return {k: v for k, v in self._instructions.items() if v}

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
        if config.transport_type == "http" and config.url:
            return HttpTransport(url=config.url, headers=config.headers)
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
