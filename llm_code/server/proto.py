"""JSON-RPC 2.0 protocol shapes for the v16 M9 formal server.

The wire format follows the JSON-RPC 2.0 spec exactly so any compliant
client can speak to ``llmcode server start``: requests carry
``{"jsonrpc": "2.0", "id": ..., "method": ..., "params": ...}``,
responses carry either ``result`` or ``error``, and notifications are
``{"jsonrpc": "2.0", "method": ..., "params": ...}`` (no ``id``).

Method catalogue::

    session.create            → create a fresh session, mint a writer token
    session.attach            → join an existing session (writer | observer)
    session.send              → forward a user message to the active writer
    session.subscribe_events  → no-op marker — events fan out automatically
    session.fork              → deep-copy a session under a new id
    session.detach            → release the caller's role on a session
    session.close             → tear down a session (writer-only)

All payloads are frozen dataclasses so mutation must round-trip through
``dataclasses.replace`` — matches the rest of the codebase's immutable
convention. Encoder/decoder helpers (``encode_*`` / ``parse_message``)
are intentionally pure: they never touch I/O so the protocol layer is
trivially unit-testable without spinning up a websocket server.
"""
from __future__ import annotations

import dataclasses
import json
from enum import Enum
from typing import Any


JSONRPC_VERSION = "2.0"


class SessionRole(str, Enum):
    """Connection role on a session.

    ``WRITER`` clients drive the conversation (one at a time per
    session); ``OBSERVER`` clients only receive events. The third role
    used internally is the broadcast notification target — it never
    appears as a user-facing role, so the enum stays at two members.
    """

    WRITER = "writer"
    OBSERVER = "observer"


class JsonRpcErrorCode(int, Enum):
    """JSON-RPC 2.0 plus llmcode-specific extensions.

    The base codes (-32700..-32000) match the spec; -32001..-32099 are
    reserved for implementation-defined errors — that's where we slot
    auth, conflict, and resumption failures.
    """

    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603

    # llmcode extensions
    UNAUTHORIZED = -32001  # 401 — bad / missing / revoked token
    WRITER_CONFLICT = -32002  # 409 — second writer attach
    SESSION_NOT_FOUND = -32003
    EVENTS_EVICTED = -32004  # client reconnected past the buffer TTL


@dataclasses.dataclass(frozen=True)
class JsonRpcError:
    """Spec-shaped error envelope; serialised under the ``error`` key."""

    code: int
    message: str
    data: Any | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": int(self.code), "message": self.message}
        if self.data is not None:
            out["data"] = self.data
        return out


@dataclasses.dataclass(frozen=True)
class JsonRpcRequest:
    """A single client→server method invocation."""

    id: int | str | None
    method: str
    params: dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "jsonrpc": JSONRPC_VERSION,
            "method": self.method,
            "params": dict(self.params),
        }
        if self.id is not None:
            out["id"] = self.id
        return out


@dataclasses.dataclass(frozen=True)
class JsonRpcResponse:
    """Server→client reply. Exactly one of ``result`` / ``error`` is set."""

    id: int | str | None
    result: Any | None = None
    error: JsonRpcError | None = None

    def __post_init__(self) -> None:
        if self.result is not None and self.error is not None:
            raise ValueError("JsonRpcResponse: result and error are mutually exclusive")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "id": self.id}
        if self.error is not None:
            out["error"] = self.error.to_dict()
        else:
            out["result"] = self.result
        return out


@dataclasses.dataclass(frozen=True)
class EventNotification:
    """Server→client event (e.g. streaming tokens, tool calls).

    ``event_id`` is monotonic per session so reconnecting clients can
    ask for ``last_event_id + 1`` and resume mid-stream without
    duplicates. ``method`` is always ``"session.event"`` — the inner
    ``params`` carry the event-specific payload.
    """

    event_id: int
    session_id: str
    payload: dict[str, Any]
    method: str = "session.event"

    def to_dict(self) -> dict[str, Any]:
        return {
            "jsonrpc": JSONRPC_VERSION,
            "method": self.method,
            "params": {
                "event_id": self.event_id,
                "session_id": self.session_id,
                "payload": dict(self.payload),
            },
        }


# ── encode/decode helpers ─────────────────────────────────────────────


def encode_request(req: JsonRpcRequest) -> str:
    return json.dumps(req.to_dict(), separators=(",", ":"))


def encode_response(resp: JsonRpcResponse) -> str:
    return json.dumps(resp.to_dict(), separators=(",", ":"))


def encode_event(event: EventNotification) -> str:
    return json.dumps(event.to_dict(), separators=(",", ":"))


def parse_message(
    raw: str,
) -> JsonRpcRequest | JsonRpcResponse | EventNotification:
    """Decode a single JSON-RPC frame and dispatch to the right shape.

    Raises :class:`ValueError` on parse / shape errors — call sites map
    that into :class:`JsonRpcError` with code ``PARSE_ERROR`` /
    ``INVALID_REQUEST`` so the wire response stays spec-compliant.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"parse error: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("frame is not a JSON object")
    if data.get("jsonrpc") != JSONRPC_VERSION:
        raise ValueError("missing or wrong 'jsonrpc' field")

    if "method" in data:
        method = str(data["method"])
        params = data.get("params") or {}
        if not isinstance(params, dict):
            raise ValueError("'params' must be an object")
        if method == "session.event":
            event_id = int(params.get("event_id", 0))
            session_id = str(params.get("session_id", ""))
            payload = params.get("payload", {})
            if not isinstance(payload, dict):
                raise ValueError("'payload' must be an object")
            return EventNotification(
                event_id=event_id,
                session_id=session_id,
                payload=dict(payload),
            )
        return JsonRpcRequest(
            id=data.get("id"),
            method=method,
            params=dict(params),
        )

    if "result" in data or "error" in data:
        err: JsonRpcError | None = None
        err_payload = data.get("error")
        if isinstance(err_payload, dict):
            err = JsonRpcError(
                code=int(err_payload.get("code", JsonRpcErrorCode.INTERNAL_ERROR)),
                message=str(err_payload.get("message", "")),
                data=err_payload.get("data"),
            )
        return JsonRpcResponse(
            id=data.get("id"),
            result=data.get("result") if err is None else None,
            error=err,
        )

    raise ValueError("frame matches neither request, response, nor event")
