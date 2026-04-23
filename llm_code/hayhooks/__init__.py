"""Hayhooks — headless transports for llmcode engine.

Provides MCP server (stdio + SSE) and OpenAI-compatible HTTP endpoint.
Install via ``pip install llmcode[hayhooks]`` for FastAPI/uvicorn/mcp deps.

Entry point: ``llmcode hayhooks serve``.

Also absorbs the legacy ``llm_code.remote`` / ``llm_code.ide`` modules
(M4.11): WebSocket IDE JSON-RPC and debug REPL are now served as
sub-apps of the hayhooks FastAPI stack.
"""
from __future__ import annotations

__all__ = [
    "hayhooks_serve",
    "HayhooksSession",
    "build_app",
    "build_mcp_server",
    "IDERpcServer",
    "JsonRpcError",
]


def __getattr__(name: str):  # pragma: no cover — lazy re-exports
    if name == "hayhooks_serve":
        from llm_code.hayhooks.cli import hayhooks_serve
        return hayhooks_serve
    if name == "HayhooksSession":
        from llm_code.hayhooks.session import HayhooksSession
        return HayhooksSession
    if name == "build_app":
        from llm_code.hayhooks.openai_compat import build_app
        return build_app
    if name == "build_mcp_server":
        from llm_code.hayhooks.mcp_transport import build_mcp_server
        return build_mcp_server
    if name == "IDERpcServer":
        from llm_code.hayhooks.ide_rpc import IDERpcServer
        return IDERpcServer
    if name == "JsonRpcError":
        from llm_code.hayhooks.ide_rpc import JsonRpcError
        return JsonRpcError
    raise AttributeError(f"module 'llm_code.hayhooks' has no attribute {name!r}")
