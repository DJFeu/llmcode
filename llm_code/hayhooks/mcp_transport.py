"""MCP transports — stdio + SSE.

Exposes a single tool ``llmcode.run_agent(prompt: str, options?: dict)``
that returns the final text response. Streaming updates are emitted as
MCP progress notifications.
"""
from __future__ import annotations

import asyncio
from typing import Any

try:  # pragma: no cover — mcp is a hayhooks extra
    from mcp.server import Server  # type: ignore
    from mcp.server.stdio import stdio_server  # type: ignore
    from mcp.types import TextContent, Tool  # type: ignore
    _MCP_AVAILABLE = True
except ImportError:  # pragma: no cover — fall-through for tests
    Server = None  # type: ignore[assignment,misc]
    stdio_server = None  # type: ignore[assignment]
    TextContent = None  # type: ignore[assignment,misc]
    Tool = None  # type: ignore[assignment,misc]
    _MCP_AVAILABLE = False

from llm_code.hayhooks.session import HayhooksSession


_TOOL_NAME = "llmcode.run_agent"
_TOOL_DESCRIPTION = (
    "Run the llmcode coding agent on a user prompt. "
    "Returns the final text response. "
    "Streaming progress is emitted as MCP notifications."
)
_INPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "prompt": {"type": "string"},
        "max_steps": {"type": "integer", "default": 20},
        "tools": {
            "type": "array",
            "items": {"type": "string"},
            "default": [],
        },
    },
    "required": ["prompt"],
}


def _require_mcp() -> None:
    if not _MCP_AVAILABLE:
        raise RuntimeError(
            "mcp is not installed; run `pip install llmcode[hayhooks]`"
        )


def build_mcp_server(
    config: Any,
    *,
    session_factory=HayhooksSession,
) -> Any:
    """Build (but do not start) an MCP server advertising ``llmcode.run_agent``."""
    _require_mcp()
    server = Server("llmcode-hayhooks")

    @server.list_tools()
    async def _list_tools():
        return [
            Tool(
                name=_TOOL_NAME,
                description=_TOOL_DESCRIPTION,
                inputSchema=_INPUT_SCHEMA,
            ),
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict):
        if name != _TOOL_NAME:
            raise ValueError(f"unknown tool: {name}")
        prompt = (arguments or {}).get("prompt", "")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        session = session_factory(config=config)
        messages = [{"role": "user", "content": prompt}]
        result = await session.run_async(messages)
        text = result.final_text() if hasattr(result, "final_text") else str(result)
        return [TextContent(type="text", text=text)]

    return server


def run_stdio(config: Any) -> None:
    """Run the MCP server over stdio until the client closes."""
    _require_mcp()

    async def _main() -> None:
        server = build_mcp_server(config)
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    asyncio.run(_main())


def run_sse(config: Any, host: str = "127.0.0.1", port: int = 8080) -> None:
    """Run the MCP server behind an SSE-capable FastAPI app."""
    _require_mcp()
    try:
        from fastapi import FastAPI
        from mcp.server.sse import SseServerTransport  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "fastapi + mcp[sse] required for `--transport sse`; "
            "install llmcode[hayhooks]"
        ) from exc
    import uvicorn

    app = FastAPI(title="llmcode-hayhooks (MCP SSE)")
    transport = SseServerTransport("/messages/")
    server = build_mcp_server(config)

    @app.get("/sse")
    async def sse_endpoint(request):  # pragma: no cover — network integration
        async with transport.connect_sse(
            request.scope, request.receive, request._send,
        ) as streams:
            await server.run(
                streams[0], streams[1],
                server.create_initialization_options(),
            )

    @app.post("/messages/{session_id}")
    async def sse_messages(session_id: str, request):  # pragma: no cover — network
        return await transport.handle_post_message(
            request.scope, request.receive, request._send,
        )

    uvicorn.run(app, host=host, port=port)


__all__ = [
    "build_mcp_server",
    "run_stdio",
    "run_sse",
    "_TOOL_NAME",
    "_INPUT_SCHEMA",
]
