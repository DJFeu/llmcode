"""MCP client implementing JSON-RPC 2.0 over an McpTransport."""
from __future__ import annotations

import itertools
from typing import Any

from .transport import McpTransport
from .types import McpResource, McpServerInfo, McpToolDefinition, McpToolResult

_PROTOCOL_VERSION = "2024-11-05"


class McpClient:
    """High-level MCP client built on top of an McpTransport."""

    def __init__(self, transport: McpTransport) -> None:
        self._transport = transport
        self._id_counter = itertools.count(1)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def initialize(self) -> McpServerInfo:
        """Send the MCP initialize request and return parsed server info."""
        result = await self._request(
            "initialize",
            {
                "protocolVersion": _PROTOCOL_VERSION,
                "clientInfo": {"name": "llm-code", "version": "0.1.0"},
                "capabilities": {},
            },
        )
        server_info = result.get("serverInfo", {})
        return McpServerInfo(
            name=server_info.get("name", ""),
            version=server_info.get("version", ""),
            capabilities=result.get("capabilities", {}),
        )

    async def list_tools(self) -> list[McpToolDefinition]:
        """Retrieve the list of tools exposed by the MCP server."""
        result = await self._request("tools/list", {})
        return [
            McpToolDefinition(
                name=tool["name"],
                description=tool.get("description", ""),
                input_schema=tool.get("inputSchema", {}),
                annotations=tool.get("annotations"),
            )
            for tool in result.get("tools", [])
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> McpToolResult:
        """Call a named tool with the given arguments."""
        result = await self._request(
            "tools/call",
            {"name": name, "arguments": arguments},
        )
        content_list = result.get("content", [])
        text = ""
        for item in content_list:
            if item.get("type") == "text":
                text = item.get("text", "")
                break
        is_error = bool(result.get("isError", False))
        return McpToolResult(content=text, is_error=is_error)

    async def list_resources(self) -> list[McpResource]:
        """Retrieve the list of resources exposed by the MCP server."""
        result = await self._request("resources/list", {})
        return [
            McpResource(
                uri=resource["uri"],
                name=resource.get("name", ""),
                description=resource.get("description"),
                mime_type=resource.get("mimeType"),
            )
            for resource in result.get("resources", [])
        ]

    async def read_resource(self, uri: str) -> str:
        """Read the content of a resource by URI, returning text."""
        result = await self._request("resources/read", {"uri": uri})
        contents = result.get("contents", [])
        for item in contents:
            if item.get("type") == "text":
                return item.get("text", "")
        return ""

    async def close(self) -> None:
        """Close the underlying transport."""
        await self._transport.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Build and send a JSON-RPC 2.0 request, returning the result dict."""
        request_id = next(self._id_counter)
        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        await self._transport.send(message)
        response = await self._transport.receive()

        if "error" in response:
            error = response["error"]
            raise RuntimeError(
                f"MCP error {error.get('code')}: {error.get('message', 'Unknown error')}"
            )

        return response.get("result", {})
