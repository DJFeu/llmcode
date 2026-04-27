"""v16 M9 — formal client/server API + session sharing.

A WebSocket + JSON-RPC 2.0 layer that lets multiple clients share one
in-flight ``llmcode`` session. One writer drives the conversation;
N observers stream the same events. Bearer-token auth + persistent
SQLite token store back the surface so a server restart does not
silently revoke every credential.

This package is intentionally separate from the legacy
``llmcode --serve`` debug REPL (``llm_code.hayhooks.debug_repl``) — the
old surface stays untouched. New CLI entry points are
``llmcode server start`` (host) and ``llmcode connect <url>`` (peer);
the JSON-RPC schema is the contract between them, declared in
:mod:`llm_code.server.proto`.

Package layout::

    proto.py    JSON-RPC 2.0 shapes (Request/Response/Notification dataclasses).
    server.py   ServerSession state machine + asyncio dispatcher.
    client.py   Python client lib + interactive ``llmcode connect`` REPL.
    tokens.py   HMAC bearer-token issuance + SQLite-backed validation.

Optional extras: install ``llmcode-cli[websocket]`` to pull
``websockets`` for real I/O. Tests exercise the protocol + state
machine directly without binding to a port.
"""
from __future__ import annotations

from llm_code.server.proto import (
    EventNotification,
    JsonRpcError,
    JsonRpcErrorCode,
    JsonRpcRequest,
    JsonRpcResponse,
    SessionRole,
    encode_event,
    encode_request,
    encode_response,
    parse_message,
)
from llm_code.server.server import (
    AttachConflict,
    ClientHandle,
    ServerSession,
    SessionManager,
)
from llm_code.server.tokens import (
    BearerToken,
    TokenStore,
    TokenValidationError,
)

__all__ = [
    "AttachConflict",
    "BearerToken",
    "ClientHandle",
    "EventNotification",
    "JsonRpcError",
    "JsonRpcErrorCode",
    "JsonRpcRequest",
    "JsonRpcResponse",
    "ServerSession",
    "SessionManager",
    "SessionRole",
    "TokenStore",
    "TokenValidationError",
    "encode_event",
    "encode_request",
    "encode_response",
    "parse_message",
]
