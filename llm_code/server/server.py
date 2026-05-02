"""Server-side session manager + JSON-RPC dispatcher (v16 M9).

The :class:`SessionManager` owns N concurrent ``ServerSession``
instances; each session tracks its writer slot, observer set, and a
ring buffer of recent ``EventNotification``\\ s for last-event-id
resumption. Method handlers live on :class:`SessionManager` itself —
``dispatch(token, request) -> response`` is the single entry point.

Why no FastAPI / Starlette? The websocket transport is a thin shell:
the protocol (parse → dispatch → emit) is what's worth testing and
must stay reusable from the in-process Python client too. Keeping the
dispatcher transport-agnostic means :func:`dispatch` is a pure async
function over the in-memory state — covered by unit tests without
binding a port.

Ring-buffer semantics: each session keeps the most recent
``EVENT_BUFFER_SIZE`` (1000) events. A reconnecting client supplies
``last_event_id``; if the server still has the next event in the
buffer it replays from there, otherwise it emits an
``EVENTS_EVICTED`` JSON-RPC error so the client knows to drop its
local state and re-attach fresh.

Writer/observer rules (spec §9 W3):

* One writer per session at a time. Second ``writer`` attach by a
  different ``client_id`` returns ``WRITER_CONFLICT`` (-32002).
* If a client attaches as ``writer`` twice from the same ``client_id``,
  it's a no-op (the implicit-detach pattern is documented in the
  acceptance tests).
* A writer attaching as ``observer`` after holding the writer slot
  releases the writer slot first, then takes the observer role.
"""
from __future__ import annotations

import asyncio
import copy
import dataclasses
import logging
import time
import uuid
from collections import deque
from typing import Any, Awaitable, Callable, Iterable

from llm_code.server.proto import (
    EventNotification,
    JsonRpcError,
    JsonRpcErrorCode,
    JsonRpcRequest,
    JsonRpcResponse,
    SessionRole,
)
from llm_code.server.tokens import (
    BearerToken,
    TokenStore,
    TokenValidationError,
    token_fingerprint,
)


logger = logging.getLogger(__name__)


EVENT_BUFFER_SIZE = 1000


class AttachConflict(Exception):
    """Raised when a second writer tries to attach to a session."""


@dataclasses.dataclass
class ClientHandle:
    """In-memory record of a connected client.

    The handle owns an ``asyncio.Queue`` so the session manager can fan
    out events without touching the websocket directly: the transport
    layer drains the queue and writes encoded frames to the wire. This
    keeps the dispatcher reusable from in-process tests (drain by
    ``await handle.queue.get()``) and websocket transport alike.
    """

    client_id: str
    role: SessionRole
    queue: asyncio.Queue
    last_event_id: int = 0


@dataclasses.dataclass
class ServerSession:
    """In-memory shared session state.

    The runtime hook (``runtime``) is intentionally Any-typed: the
    session manager calls ``runtime.send_user_message(text)`` when a
    writer issues ``session.send`` and treats the runtime as opaque.
    Tests inject a stub runtime so the protocol layer is exercised
    without spinning up the full conversation engine.
    """

    session_id: str
    created_at: float
    runtime: Any | None = None
    writer_client_id: str | None = None
    observers: dict[str, ClientHandle] = dataclasses.field(default_factory=dict)
    next_event_id: int = 1
    event_buffer: deque = dataclasses.field(default_factory=lambda: deque(maxlen=EVENT_BUFFER_SIZE))
    closed: bool = False

    def has_client(self, client_id: str) -> bool:
        return client_id in self.observers

    def all_clients(self) -> Iterable[ClientHandle]:
        return list(self.observers.values())


# ── manager ───────────────────────────────────────────────────────────


class SessionManager:
    """Holds ``ServerSession`` instances and dispatches JSON-RPC requests.

    The manager is shared between every client connection. Asyncio
    locks keep concurrent ``attach`` / ``detach`` / ``send`` from
    racing on the writer slot or event buffer. A token store is
    injected so the auth surface stays testable in isolation.
    """

    def __init__(
        self,
        tokens: TokenStore,
        runtime_factory: Callable[[str], Awaitable[Any]] | None = None,
    ) -> None:
        self._sessions: dict[str, ServerSession] = {}
        self._tokens = tokens
        self._runtime_factory = runtime_factory
        self._lock = asyncio.Lock()

    # ── lifecycle ────────────────────────────────────────────────────

    def get(self, session_id: str) -> ServerSession | None:
        return self._sessions.get(session_id)

    async def create_session(self, runtime: Any | None = None) -> ServerSession:
        """Create a fresh session with a random id."""
        session_id = uuid.uuid4().hex[:12]
        if runtime is None and self._runtime_factory is not None:
            runtime = await self._runtime_factory(session_id)
        session = ServerSession(
            session_id=session_id,
            created_at=time.time(),
            runtime=runtime,
        )
        async with self._lock:
            self._sessions[session_id] = session
        logger.info("server.protocol session_created id=%s", session_id)
        return session

    async def fork_session(self, source_id: str) -> ServerSession:
        """Deep-copy ``source_id`` under a new id; raise on missing source."""
        source = self.get(source_id)
        if source is None or source.closed:
            raise KeyError(source_id)
        forked_id = uuid.uuid4().hex[:12]
        # Deep copy the runtime so child mutations don't leak; observers
        # and event buffer start fresh because they are transport state.
        if source.runtime is not None and hasattr(source.runtime, "fork_for_session"):
            forked_runtime = source.runtime.fork_for_session(forked_id)
        else:
            forked_runtime = copy.deepcopy(source.runtime) if source.runtime is not None else None
        session = ServerSession(
            session_id=forked_id,
            created_at=time.time(),
            runtime=forked_runtime,
        )
        async with self._lock:
            self._sessions[forked_id] = session
        logger.info(
            "server.protocol session_forked parent=%s child=%s",
            source_id,
            forked_id,
        )
        return session

    async def close_session(self, session_id: str) -> None:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            session.closed = True
            self._sessions.pop(session_id, None)
        logger.info("server.protocol session_closed id=%s", session_id)

    # ── auth ─────────────────────────────────────────────────────────

    def authenticate(self, token: str | None) -> BearerToken:
        """Validate a token; raise :class:`TokenValidationError` on any failure."""
        if not token:
            raise TokenValidationError("missing token")
        return self._tokens.validate(token)

    # ── attach / detach ──────────────────────────────────────────────

    async def attach(
        self,
        session_id: str,
        client_id: str,
        role: SessionRole,
        queue: asyncio.Queue | None = None,
    ) -> ClientHandle:
        """Register a client on a session under the given role.

        Returns the :class:`ClientHandle` that the transport layer
        should drain. Raises :class:`AttachConflict` on writer
        contention. Re-attach by the same client_id is idempotent —
        role can be downgraded (writer→observer) or upgraded
        (observer→writer if the writer slot is empty).
        """
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.closed:
                raise KeyError(session_id)

            existing = session.observers.get(client_id)
            handle_queue = (
                queue if queue is not None
                else (existing.queue if existing is not None else asyncio.Queue())
            )

            if role == SessionRole.WRITER:
                if (
                    session.writer_client_id is not None
                    and session.writer_client_id != client_id
                ):
                    raise AttachConflict(session_id)
                session.writer_client_id = client_id
            else:
                # role == observer; release the writer slot if this
                # client_id was previously the writer.
                if session.writer_client_id == client_id:
                    session.writer_client_id = None

            handle = ClientHandle(
                client_id=client_id,
                role=role,
                queue=handle_queue,
                last_event_id=existing.last_event_id if existing else 0,
            )
            session.observers[client_id] = handle
        logger.info(
            "server.protocol session_attached id=%s client=%s role=%s",
            session_id,
            client_id,
            role.value,
        )
        return handle

    async def detach(self, session_id: str, client_id: str) -> None:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            if session.writer_client_id == client_id:
                session.writer_client_id = None
            session.observers.pop(client_id, None)
        logger.info(
            "server.protocol session_detached id=%s client=%s",
            session_id,
            client_id,
        )

    # ── events ───────────────────────────────────────────────────────

    async def emit_event(self, session_id: str, payload: dict[str, Any]) -> EventNotification:
        """Append an event to the session's ring buffer and fan out to clients."""
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.closed:
                raise KeyError(session_id)
            event = EventNotification(
                event_id=session.next_event_id,
                session_id=session_id,
                payload=dict(payload),
            )
            session.next_event_id += 1
            session.event_buffer.append(event)
            targets = list(session.observers.values())
        for handle in targets:
            try:
                handle.queue.put_nowait(event)
            except asyncio.QueueFull:  # pragma: no cover — Queue() default is unbounded
                logger.warning(
                    "server.protocol queue_full client=%s session=%s",
                    handle.client_id,
                    session_id,
                )
        return event

    async def replay_after(
        self, session_id: str, last_event_id: int
    ) -> list[EventNotification] | None:
        """Return events with id > ``last_event_id`` from the buffer.

        Returns ``None`` when the requested cursor is older than the
        oldest buffered event — caller must surface ``EVENTS_EVICTED``.
        """
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if not session.event_buffer:
                # Nothing in buffer; only valid resume is "none missed"
                return [] if last_event_id < session.next_event_id else None
            oldest = session.event_buffer[0].event_id
            if last_event_id + 1 < oldest:
                return None
            return [e for e in session.event_buffer if e.event_id > last_event_id]

    # ── dispatch ─────────────────────────────────────────────────────

    async def dispatch(
        self,
        token: str | None,
        request: JsonRpcRequest,
        client_id: str,
    ) -> JsonRpcResponse:
        """Authenticate + route a single JSON-RPC request.

        Note: every call re-validates the token against the DB so
        revocation lands on the next method invocation, not on the
        next reconnect.
        """
        try:
            bearer = self.authenticate(token)
        except TokenValidationError as exc:
            logger.info(
                "server.protocol unauthorized token=%s reason=%s",
                token_fingerprint(token) if token else "<none>",
                exc,
            )
            return JsonRpcResponse(
                id=request.id,
                error=JsonRpcError(
                    code=JsonRpcErrorCode.UNAUTHORIZED.value,
                    message=str(exc),
                ),
            )

        method = request.method
        try:
            if method == "session.create":
                return await self._m_create(request, bearer, client_id)
            if method == "session.attach":
                return await self._m_attach(request, bearer, client_id)
            if method == "session.send":
                return await self._m_send(request, bearer, client_id)
            if method == "session.subscribe_events":
                return await self._m_subscribe(request, bearer, client_id)
            if method == "session.fork":
                return await self._m_fork(request, bearer, client_id)
            if method == "session.detach":
                return await self._m_detach(request, bearer, client_id)
            if method == "session.close":
                return await self._m_close(request, bearer, client_id)
            return JsonRpcResponse(
                id=request.id,
                error=JsonRpcError(
                    code=JsonRpcErrorCode.METHOD_NOT_FOUND.value,
                    message=f"unknown method: {method}",
                ),
            )
        except AttachConflict:
            return JsonRpcResponse(
                id=request.id,
                error=JsonRpcError(
                    code=JsonRpcErrorCode.WRITER_CONFLICT.value,
                    message="writer slot already held",
                ),
            )
        except KeyError as exc:
            return JsonRpcResponse(
                id=request.id,
                error=JsonRpcError(
                    code=JsonRpcErrorCode.SESSION_NOT_FOUND.value,
                    message=f"session not found: {exc}",
                ),
            )
        except Exception as exc:  # noqa: BLE001 — defensive
            logger.exception("dispatch failed: %s", exc)
            return JsonRpcResponse(
                id=request.id,
                error=JsonRpcError(
                    code=JsonRpcErrorCode.INTERNAL_ERROR.value,
                    message=str(exc),
                ),
            )

    # ── method handlers ──────────────────────────────────────────────

    async def _m_create(
        self,
        request: JsonRpcRequest,
        bearer: BearerToken,
        client_id: str,
    ) -> JsonRpcResponse:
        # Token's session_id is "*" when minted as an admin grant; any
        # other value pins the caller to that session id.
        if bearer.session_id != "*" and bearer.session_id:
            return JsonRpcResponse(
                id=request.id,
                error=JsonRpcError(
                    code=JsonRpcErrorCode.UNAUTHORIZED.value,
                    message="token is scoped to a specific session; cannot create new",
                ),
            )
        session = await self.create_session()
        return JsonRpcResponse(
            id=request.id,
            result={"session_id": session.session_id, "created_at": session.created_at},
        )

    async def _m_attach(
        self,
        request: JsonRpcRequest,
        bearer: BearerToken,
        client_id: str,
    ) -> JsonRpcResponse:
        session_id = str(request.params.get("session_id") or bearer.session_id)
        role_raw = str(request.params.get("role") or bearer.role.value)
        last_event_id = int(request.params.get("last_event_id") or 0)
        try:
            role = SessionRole(role_raw)
        except ValueError:
            return JsonRpcResponse(
                id=request.id,
                error=JsonRpcError(
                    code=JsonRpcErrorCode.INVALID_PARAMS.value,
                    message=f"unknown role: {role_raw}",
                ),
            )
        if (
            bearer.session_id not in ("*", "", session_id)
            or (bearer.role == SessionRole.OBSERVER and role == SessionRole.WRITER)
        ):
            return JsonRpcResponse(
                id=request.id,
                error=JsonRpcError(
                    code=JsonRpcErrorCode.UNAUTHORIZED.value,
                    message="token does not authorize that session/role",
                ),
            )

        handle = await self.attach(session_id, client_id, role)

        replayed: list[dict] | None = None
        if last_event_id > 0:
            events = await self.replay_after(session_id, last_event_id)
            if events is None:
                # buffer evicted the cursor — caller must reset
                await self.detach(session_id, client_id)
                return JsonRpcResponse(
                    id=request.id,
                    error=JsonRpcError(
                        code=JsonRpcErrorCode.EVENTS_EVICTED.value,
                        message="events evicted; full re-attach required",
                    ),
                )
            replayed = [e.to_dict()["params"] for e in events]
            for e in events:
                handle.queue.put_nowait(e)
        return JsonRpcResponse(
            id=request.id,
            result={
                "session_id": session_id,
                "client_id": client_id,
                "role": handle.role.value,
                "replayed": replayed or [],
            },
        )

    async def _m_send(
        self,
        request: JsonRpcRequest,
        bearer: BearerToken,
        client_id: str,
    ) -> JsonRpcResponse:
        session_id = str(request.params.get("session_id") or bearer.session_id)
        text = str(request.params.get("text") or "")
        session = self.get(session_id)
        if session is None or session.closed:
            return JsonRpcResponse(
                id=request.id,
                error=JsonRpcError(
                    code=JsonRpcErrorCode.SESSION_NOT_FOUND.value,
                    message=f"session not found: {session_id}",
                ),
            )
        if session.writer_client_id != client_id or bearer.role != SessionRole.WRITER:
            return JsonRpcResponse(
                id=request.id,
                error=JsonRpcError(
                    code=JsonRpcErrorCode.UNAUTHORIZED.value,
                    message="not the writer for this session",
                ),
            )

        # Fan out a "user_message" event so observers see the prompt.
        event = await self.emit_event(
            session_id, {"type": "user_message", "text": text}
        )

        # Optionally call into the runtime; the runtime is responsible
        # for emitting follow-up events (token streams, tool calls, ...)
        # by calling back into ``manager.emit_event``. Tests typically
        # leave runtime=None so they only see the user_message event.
        if session.runtime is not None and hasattr(session.runtime, "send_user_message"):
            try:
                maybe = session.runtime.send_user_message(text)
                if asyncio.iscoroutine(maybe):
                    asyncio.create_task(maybe)
            except Exception as exc:  # noqa: BLE001 — runtime owns errors
                logger.exception("runtime.send_user_message raised: %s", exc)

        return JsonRpcResponse(
            id=request.id,
            result={"accepted": True, "event_id": event.event_id},
        )

    async def _m_subscribe(
        self,
        request: JsonRpcRequest,
        bearer: BearerToken,
        client_id: str,
    ) -> JsonRpcResponse:
        # No-op: ``attach`` already wires the queue. This method exists
        # so a client can re-arm subscription explicitly after a
        # protocol upgrade without re-attaching.
        return JsonRpcResponse(id=request.id, result={"ok": True})

    async def _m_fork(
        self,
        request: JsonRpcRequest,
        bearer: BearerToken,
        client_id: str,
    ) -> JsonRpcResponse:
        source_id = str(request.params.get("session_id") or bearer.session_id)
        if bearer.session_id not in ("*", "", source_id):
            return JsonRpcResponse(
                id=request.id,
                error=JsonRpcError(
                    code=JsonRpcErrorCode.UNAUTHORIZED.value,
                    message="token does not authorize forking that session",
                ),
            )
        forked = await self.fork_session(source_id)
        return JsonRpcResponse(
            id=request.id,
            result={"session_id": forked.session_id, "parent_id": source_id},
        )

    async def _m_detach(
        self,
        request: JsonRpcRequest,
        bearer: BearerToken,
        client_id: str,
    ) -> JsonRpcResponse:
        session_id = str(request.params.get("session_id") or bearer.session_id)
        await self.detach(session_id, client_id)
        return JsonRpcResponse(id=request.id, result={"ok": True})

    async def _m_close(
        self,
        request: JsonRpcRequest,
        bearer: BearerToken,
        client_id: str,
    ) -> JsonRpcResponse:
        session_id = str(request.params.get("session_id") or bearer.session_id)
        session = self.get(session_id)
        if session is None:
            return JsonRpcResponse(
                id=request.id,
                error=JsonRpcError(
                    code=JsonRpcErrorCode.SESSION_NOT_FOUND.value,
                    message=f"session not found: {session_id}",
                ),
            )
        if session.writer_client_id != client_id or bearer.role != SessionRole.WRITER:
            return JsonRpcResponse(
                id=request.id,
                error=JsonRpcError(
                    code=JsonRpcErrorCode.UNAUTHORIZED.value,
                    message="only the writer may close a session",
                ),
            )
        await self.close_session(session_id)
        return JsonRpcResponse(id=request.id, result={"closed": True})
