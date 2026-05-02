"""WebSocket bind layer for :mod:`llm_code.server.server`.

Splits transport from protocol so the dispatcher (``SessionManager``)
stays unit-testable without a port. The websocket layer:

1. Accepts the bearer token from the ``Authorization: Bearer <token>``
   header (or ``?token=<...>`` query string fallback).
2. Spawns one outbound writer task per connection that drains the
   client's :class:`asyncio.Queue` and sends encoded events.
3. Reads inbound frames, parses them via
   :func:`llm_code.server.proto.parse_message`, and routes them to
   :meth:`SessionManager.dispatch`.

The transport never logs full tokens — only the 8-char fingerprint
from :func:`token_fingerprint` to satisfy the M9 R2 mitigation.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import urllib.parse
import uuid
from typing import Any, Callable

from llm_code.server.proto import (
    EventNotification,
    JsonRpcError,
    JsonRpcErrorCode,
    JsonRpcResponse,
    encode_event,
    encode_response,
    parse_message,
)
from llm_code.server.server import SessionManager
from llm_code.server.tokens import token_fingerprint


logger = logging.getLogger(__name__)


def _bound_port(server: Any, fallback: int) -> int:
    sockets = getattr(server, "sockets", None) or []
    for sock in sockets:
        try:
            sockname = sock.getsockname()
        except OSError:
            continue
        if isinstance(sockname, tuple) and len(sockname) >= 2:
            port = sockname[1]
            if isinstance(port, int):
                return port
    return fallback


def _extract_token(connection: Any, path: str | None = None) -> str | None:
    headers = getattr(connection, "request_headers", None) or getattr(connection, "headers", None)
    if headers is not None:
        try:
            auth = headers.get("Authorization") or headers.get("authorization")
        except AttributeError:
            auth = None
        if auth and auth.lower().startswith("bearer "):
            return auth[7:].strip()
    target_path = path
    if target_path is None:
        target_path = getattr(connection, "path", "")
    if target_path and "?" in target_path:
        query = target_path.split("?", 1)[1]
        params = urllib.parse.parse_qs(query)
        token_values = params.get("token") or []
        if token_values:
            return token_values[0]
    return None


async def _writer_task(connection: Any, queue: asyncio.Queue, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            item = await queue.get()
        except asyncio.CancelledError:
            return
        if isinstance(item, EventNotification):
            try:
                await connection.send(encode_event(item))
            except Exception as exc:  # noqa: BLE001
                logger.info("server.transport writer_failed: %s", exc)
                return


async def handle_connection(connection: Any, manager: SessionManager) -> None:
    """Per-connection dispatch loop.

    The function is exported so tests can drive it with an asyncio
    in-memory transport without a real websocket. Real callers
    typically pass ``websockets.WebSocketServerProtocol``.
    """
    token = _extract_token(connection)
    fp = token_fingerprint(token) if token else "<none>"
    if not token:
        await connection.send(
            encode_response(
                JsonRpcResponse(
                    id=None,
                    error=JsonRpcError(
                        code=JsonRpcErrorCode.UNAUTHORIZED.value,
                        message="missing bearer token",
                    ),
                )
            )
        )
        return

    client_id = uuid.uuid4().hex[:12]
    queue: asyncio.Queue = asyncio.Queue()
    stop = asyncio.Event()
    writer = asyncio.create_task(_writer_task(connection, queue, stop))
    logger.info(
        "server.transport client_connected client=%s token=%s",
        client_id,
        fp,
    )

    attached_session: str | None = None
    try:
        async for raw in connection:
            try:
                msg = parse_message(raw)
            except ValueError as exc:
                await connection.send(
                    encode_response(
                        JsonRpcResponse(
                            id=None,
                            error=JsonRpcError(
                                code=JsonRpcErrorCode.PARSE_ERROR.value,
                                message=str(exc),
                            ),
                        )
                    )
                )
                continue
            if not hasattr(msg, "method") or not hasattr(msg, "id"):
                continue
            if msg.method.startswith("session.event"):
                # clients don't push events to the server
                continue
            response = await manager.dispatch(token, msg, client_id)
            await connection.send(encode_response(response))

            # Wire the queue when the client successfully attaches.
            if (
                msg.method == "session.attach"
                and response.error is None
                and response.result
            ):
                attached_session = response.result.get("session_id")
                if attached_session:
                    handle = manager.get(attached_session)
                    if handle is not None and client_id in handle.observers:
                        handle.observers[client_id].queue = queue
            if msg.method == "session.detach":
                attached_session = None
    except Exception as exc:  # noqa: BLE001 — connection died
        logger.info(
            "server.transport client_dropped client=%s reason=%s",
            client_id,
            exc,
        )
    finally:
        stop.set()
        writer.cancel()
        if attached_session is not None:
            await manager.detach(attached_session, client_id)


async def serve(
    host: str,
    port: int,
    manager: SessionManager,
    on_listen: Callable[[str, int], Any] | None = None,
) -> None:
    """Bind a websocket server. Blocks until cancelled."""
    import websockets  # type: ignore[import-untyped]

    async def _handler(connection: Any) -> None:  # websockets >= 12 single-arg shape
        await handle_connection(connection, manager)

    async with websockets.serve(_handler, host=host, port=port) as ws_server:
        actual_port = _bound_port(ws_server, port)
        if on_listen is not None:
            result = on_listen(host, actual_port)
            if inspect.isawaitable(result):
                await result
        await asyncio.Future()  # run forever
