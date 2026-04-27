"""Protocol shape tests for v16 M9 (JSON-RPC 2.0 over WebSocket).

These tests exercise the encode/decode primitives and the
``SessionManager.dispatch`` entry point without binding a port. They
guarantee that:

* every method round-trips through ``parse_message`` cleanly;
* a missing/bad token surfaces ``JsonRpcErrorCode.UNAUTHORIZED``;
* unknown methods return ``METHOD_NOT_FOUND``;
* ``session.create`` mints a session and ``session.close`` closes it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

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
from llm_code.server.server import SessionManager
from llm_code.server.tokens import TokenStore


@pytest.fixture
def store(tmp_path: Path) -> TokenStore:
    return TokenStore(tmp_path / "tokens.db")


@pytest.fixture
def manager(store: TokenStore) -> SessionManager:
    return SessionManager(tokens=store)


# ── encoding round trips ──────────────────────────────────────────────


def test_request_encode_decode_round_trip() -> None:
    req = JsonRpcRequest(id=1, method="session.create", params={"k": "v"})
    decoded = parse_message(encode_request(req))
    assert isinstance(decoded, JsonRpcRequest)
    assert decoded.id == 1
    assert decoded.method == "session.create"
    assert decoded.params == {"k": "v"}


def test_response_encode_decode_with_result() -> None:
    resp = JsonRpcResponse(id=1, result={"ok": True})
    decoded = parse_message(encode_response(resp))
    assert isinstance(decoded, JsonRpcResponse)
    assert decoded.id == 1
    assert decoded.result == {"ok": True}
    assert decoded.error is None


def test_response_encode_decode_with_error() -> None:
    resp = JsonRpcResponse(
        id=1,
        error=JsonRpcError(code=-32001, message="bad token"),
    )
    decoded = parse_message(encode_response(resp))
    assert isinstance(decoded, JsonRpcResponse)
    assert decoded.error is not None
    assert decoded.error.code == -32001
    assert decoded.error.message == "bad token"


def test_event_encode_decode() -> None:
    event = EventNotification(
        event_id=42, session_id="abc", payload={"type": "tick"}
    )
    decoded = parse_message(encode_event(event))
    assert isinstance(decoded, EventNotification)
    assert decoded.event_id == 42
    assert decoded.session_id == "abc"
    assert decoded.payload == {"type": "tick"}


def test_response_with_both_result_and_error_rejected() -> None:
    with pytest.raises(ValueError):
        JsonRpcResponse(id=1, result={"ok": True}, error=JsonRpcError(0, "x"))


def test_parse_rejects_non_object() -> None:
    with pytest.raises(ValueError):
        parse_message(json.dumps([1, 2, 3]))


def test_parse_rejects_wrong_jsonrpc_version() -> None:
    with pytest.raises(ValueError):
        parse_message(json.dumps({"jsonrpc": "1.0", "method": "x"}))


def test_parse_rejects_unknown_shape() -> None:
    with pytest.raises(ValueError):
        parse_message(json.dumps({"jsonrpc": "2.0", "id": 1}))


# ── dispatcher behaviour ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_missing_token_returns_unauthorized(
    manager: SessionManager,
) -> None:
    req = JsonRpcRequest(id=1, method="session.create")
    resp = await manager.dispatch(token=None, request=req, client_id="c1")
    assert resp.error is not None
    assert resp.error.code == JsonRpcErrorCode.UNAUTHORIZED.value


@pytest.mark.asyncio
async def test_dispatch_unknown_method(
    manager: SessionManager, store: TokenStore
) -> None:
    bearer = store.grant("*", SessionRole.WRITER)
    resp = await manager.dispatch(
        token=bearer.token,
        request=JsonRpcRequest(id=1, method="session.unknown"),
        client_id="c1",
    )
    assert resp.error is not None
    assert resp.error.code == JsonRpcErrorCode.METHOD_NOT_FOUND.value


@pytest.mark.asyncio
async def test_session_create_then_close(
    manager: SessionManager, store: TokenStore
) -> None:
    bearer = store.grant("*", SessionRole.WRITER)
    create = await manager.dispatch(
        token=bearer.token,
        request=JsonRpcRequest(id=1, method="session.create"),
        client_id="c1",
    )
    assert create.error is None
    session_id = create.result["session_id"]

    # Re-mint a session-scoped writer token so close passes auth.
    sess_bearer = store.grant(session_id, SessionRole.WRITER)

    attach = await manager.dispatch(
        token=sess_bearer.token,
        request=JsonRpcRequest(
            id=2, method="session.attach",
            params={"session_id": session_id, "role": "writer"},
        ),
        client_id="c1",
    )
    assert attach.error is None

    close = await manager.dispatch(
        token=sess_bearer.token,
        request=JsonRpcRequest(id=3, method="session.close"),
        client_id="c1",
    )
    assert close.error is None
    assert close.result == {"closed": True}
    assert manager.get(session_id) is None


@pytest.mark.asyncio
async def test_session_send_requires_writer_role(
    manager: SessionManager, store: TokenStore
) -> None:
    create_token = store.grant("*", SessionRole.WRITER)
    create = await manager.dispatch(
        token=create_token.token,
        request=JsonRpcRequest(id=1, method="session.create"),
        client_id="admin",
    )
    session_id = create.result["session_id"]

    # Observer cannot send.
    obs_token = store.grant(session_id, SessionRole.OBSERVER)
    await manager.dispatch(
        token=obs_token.token,
        request=JsonRpcRequest(
            id=2, method="session.attach",
            params={"session_id": session_id, "role": "observer"},
        ),
        client_id="obs",
    )
    send = await manager.dispatch(
        token=obs_token.token,
        request=JsonRpcRequest(
            id=3, method="session.send",
            params={"session_id": session_id, "text": "hi"},
        ),
        client_id="obs",
    )
    assert send.error is not None
    assert send.error.code == JsonRpcErrorCode.UNAUTHORIZED.value
