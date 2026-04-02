"""McpToolBridge: wraps an MCP tool as a Tool ABC."""
from __future__ import annotations

import asyncio
import concurrent.futures

from llm_code.mcp.client import McpClient
from llm_code.mcp.types import McpToolDefinition
from llm_code.tools.base import PermissionLevel, Tool, ToolResult


class McpToolBridge(Tool):
    """Adapts a remote MCP tool to the local Tool ABC."""

    def __init__(
        self,
        server_name: str,
        mcp_tool: McpToolDefinition,
        client: McpClient,
    ) -> None:
        self._server_name = server_name
        self._mcp_tool = mcp_tool
        self._client = client

    # ------------------------------------------------------------------
    # Tool ABC — identity & schema
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return f"mcp__{self._server_name}__{self._mcp_tool.name}"

    @property
    def description(self) -> str:
        return self._mcp_tool.description

    @property
    def input_schema(self) -> dict:
        return self._mcp_tool.input_schema

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.FULL_ACCESS

    # ------------------------------------------------------------------
    # Tool ABC — behaviour flags
    # ------------------------------------------------------------------

    def is_read_only(self, args: dict) -> bool:
        return bool((self._mcp_tool.annotations or {}).get("readOnly", False))

    def is_destructive(self, args: dict) -> bool:
        return bool((self._mcp_tool.annotations or {}).get("destructive", False))

    def is_concurrency_safe(self, args: dict) -> bool:
        return self.is_read_only(args)

    # ------------------------------------------------------------------
    # Tool ABC — execution
    # ------------------------------------------------------------------

    def execute(self, args: dict) -> ToolResult:
        """Call the remote MCP tool synchronously."""
        try:
            mcp_result = self._run_async(self._client.call_tool(self._mcp_tool.name, args))
            return ToolResult(output=mcp_result.content, is_error=mcp_result.is_error)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(output=str(exc), is_error=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _run_async(coro):  # type: ignore[type-arg]
        """Run an async coroutine from a sync context, even inside an event loop."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
        else:
            return asyncio.run(coro)
