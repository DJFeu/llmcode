"""MCP protocol types as frozen dataclasses."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class McpServerConfig:
    """Configuration for connecting to an MCP server."""

    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] | None = None
    transport_type: str = "stdio"
    url: str | None = None
    headers: dict[str, str] | None = None


@dataclass(frozen=True)
class McpToolDefinition:
    """Definition of a tool exposed by an MCP server."""

    name: str
    description: str
    input_schema: dict
    annotations: dict | None = None


@dataclass(frozen=True)
class McpToolResult:
    """Result returned from calling an MCP tool."""

    content: str
    is_error: bool = False


@dataclass(frozen=True)
class McpResource:
    """A resource exposed by an MCP server."""

    uri: str
    name: str
    description: str | None = None
    mime_type: str | None = None


@dataclass(frozen=True)
class McpServerInfo:
    """Information about an MCP server returned during initialization."""

    name: str
    version: str
    capabilities: dict
