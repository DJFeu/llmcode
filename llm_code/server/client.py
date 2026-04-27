"""Python client lib for the v16 M9 formal server.

The :class:`ServerClient` wraps a websocket connection plus the JSON-RPC
request/response correlation table. Methods like ``attach`` and
``send`` return decoded :class:`JsonRpcResponse` objects; events are
delivered via :meth:`subscribe_events` (an async iterator) so callers
can consume tokens streamed from the server one event at a time.

Reconnect strategy: on transient failures (``ConnectionClosed``,
``OSError``) the client backs off exponentially up to 60 seconds and
re-attaches with the last seen ``event_id``. If the server returns
``EVENTS_EVICTED`` we fall back to a clean re-attach (caller is
notified via the public ``on_evicted`` callback).

The ``websockets`` package is imported lazily inside :meth:`connect`
so the protocol primitives stay importable in environments where the
optional ``[websocket]`` extra is not installed.
"""
from __future__ import annotations

import asyncio
import dataclasses
import itertools
import logging
import uuid
from typing import Any, AsyncIterator, Awaitable, Callable

from llm_code.server.proto import (
    EventNotification,
    JsonRpcErrorCode,
    JsonRpcRequest,
    JsonRpcResponse,
    SessionRole,
    encode_request,
    parse_message,
)


logger = logging.getLogger(__name__)


@dataclasses.dataclass
class ClientConfig:
    url: str
    token: str
    client_id: str = ""
    initial_role: SessionRole = SessionRole.OBSERVER
    session_id: str | None = None
    max_backoff: float = 60.0


class ServerClient:
    """Async WebSocket client with reconnect + event resumption."""

    def __init__(self, config: ClientConfig) -> None:
        self._config = config
        self._client_id = config.client_id or uuid.uuid4().hex[:12]
        self._counter = itertools.count(1)
        self._pending: dict[Any, asyncio.Future] = {}
        self._events: asyncio.Queue[EventNotification] = asyncio.Queue()
        self._ws: Any = None
        self._reader_task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._last_event_id = 0
        self.on_evicted: Callable[[], Awaitable[None]] | None = None

    @property
    def client_id(self) -> str:
        return self._client_id

    @property
    def last_event_id(self) -> int:
        return self._last_event_id

    # ── connection ───────────────────────────────────────────────────

    async def connect(self) -> None:
        try:
            import websockets  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError(
                "WebSocket transport requires the 'websockets' package: "
                "pip install llmcode-cli[websocket]"
            ) from exc
        url = self._config.url
        headers = {"Authorization": f"Bearer {self._config.token}"}
        # ``additional_headers`` works on websockets >= 12; fall back to
        # ``extra_headers`` for older builds. Either way the server
        # accepts the bearer token via either header path.
        try:
            self._ws = await websockets.connect(
                url, additional_headers=headers,
            )
        except TypeError:
            self._ws = await websockets.connect(
                url, extra_headers=headers,
            )
        self._reader_task = asyncio.create_task(self._reader_loop())
        logger.info(
            "server.client connected url=%s client_id=%s",
            url,
            self._client_id,
        )

    async def close(self) -> None:
        self._stop.set()
        if self._reader_task is not None:
            self._reader_task.cancel()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001 — defensive on shutdown
                pass

    # ── reader loop ──────────────────────────────────────────────────

    async def _reader_loop(self) -> None:
        try:
            async for raw in self._ws:
                self._dispatch_inbound(raw)
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001 — caller may reconnect
            logger.info("server.client reader exited: %s", exc)

    def _dispatch_inbound(self, raw: str) -> None:
        try:
            msg = parse_message(raw)
        except ValueError as exc:
            logger.warning("server.client parse_error: %s", exc)
            return
        if isinstance(msg, EventNotification):
            self._last_event_id = max(self._last_event_id, msg.event_id)
            try:
                self._events.put_nowait(msg)
            except asyncio.QueueFull:  # pragma: no cover — Queue() unbounded
                logger.warning("server.client event_queue_full")
            return
        if isinstance(msg, JsonRpcResponse):
            future = self._pending.pop(msg.id, None)
            if future is not None and not future.done():
                future.set_result(msg)
            return
        # Server-side requests are not part of the M9 surface.
        logger.debug("server.client ignored_request method=%s", getattr(msg, "method", "?"))

    # ── method calls ─────────────────────────────────────────────────

    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float = 10.0,
    ) -> JsonRpcResponse:
        if self._ws is None:
            raise RuntimeError("client not connected")
        request_id = next(self._counter)
        request = JsonRpcRequest(id=request_id, method=method, params=params or {})
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[request_id] = future
        await self._ws.send(encode_request(request))
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(request_id, None)

    async def attach(
        self,
        session_id: str,
        role: SessionRole | None = None,
    ) -> JsonRpcResponse:
        params = {
            "session_id": session_id,
            "role": (role or self._config.initial_role).value,
            "last_event_id": self._last_event_id,
        }
        resp = await self.call("session.attach", params)
        if resp.error is not None and resp.error.code == JsonRpcErrorCode.EVENTS_EVICTED.value:
            self._last_event_id = 0
            if self.on_evicted is not None:
                await self.on_evicted()
            params["last_event_id"] = 0
            resp = await self.call("session.attach", params)
        return resp

    async def create(self) -> JsonRpcResponse:
        return await self.call("session.create", {})

    async def send(self, session_id: str, text: str) -> JsonRpcResponse:
        return await self.call(
            "session.send",
            {"session_id": session_id, "text": text},
        )

    async def fork(self, session_id: str) -> JsonRpcResponse:
        return await self.call("session.fork", {"session_id": session_id})

    async def detach(self, session_id: str) -> JsonRpcResponse:
        return await self.call("session.detach", {"session_id": session_id})

    async def close_session(self, session_id: str) -> JsonRpcResponse:
        return await self.call("session.close", {"session_id": session_id})

    # ── events ───────────────────────────────────────────────────────

    async def subscribe_events(self) -> AsyncIterator[EventNotification]:
        while not self._stop.is_set():
            event = await self._events.get()
            yield event

    # ── reconnect helper ─────────────────────────────────────────────

    async def reconnect_with_resume(
        self,
        session_id: str,
        role: SessionRole | None = None,
    ) -> JsonRpcResponse:
        """Reconnect the websocket, then attach with ``last_event_id``.

        Callers that hold the writer slot should pass ``role=WRITER``;
        observers can leave the default. The method does not retry on
        its own — the enclosing reconnect loop owns the backoff.
        """
        await self.close()
        self._stop.clear()
        await self.connect()
        return await self.attach(session_id, role=role)


# ── interactive CLI command ───────────────────────────────────────────


async def run_interactive_client(
    url: str,
    token: str,
    role: SessionRole = SessionRole.WRITER,
    session_id: str | None = None,
) -> int:
    """Launch ``llmcode connect <url>`` interactive session.

    Reads lines from stdin, sends them via ``session.send``, prints
    streamed events to stdout. Designed for smoke tests; the real REPL
    (``llmcode connect ...``) wraps this with a proper prompt_toolkit
    surface but the in-process Python lib is the same.
    """
    config = ClientConfig(
        url=url, token=token, initial_role=role, session_id=session_id,
    )
    client = ServerClient(config)
    await client.connect()

    target_session = session_id
    if target_session is None:
        resp = await client.create()
        if resp.error is not None:
            print(f"create failed: {resp.error.message}")
            return 1
        target_session = resp.result["session_id"]

    attach_resp = await client.attach(target_session, role=role)
    if attach_resp.error is not None:
        print(f"attach failed: {attach_resp.error.message}")
        return 1

    async def _print_events() -> None:
        async for event in client.subscribe_events():
            print(f"[event {event.event_id}] {event.payload}")

    printer = asyncio.create_task(_print_events())
    try:
        loop = asyncio.get_event_loop()
        while True:
            line = await loop.run_in_executor(None, input, "> ")
            if not line:
                continue
            if line.strip() in {":q", ":quit", ":exit"}:
                break
            await client.send(target_session, line)
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        printer.cancel()
        await client.close()
    return 0
