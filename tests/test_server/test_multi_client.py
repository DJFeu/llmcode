"""Multi-client semantics for the v16 M9 formal server.

Covers the high-leverage acceptance scenarios from the plan:

* Two clients attach to one session; writer sends, both see events.
* Second writer attach by a different client_id → 409 (WRITER_CONFLICT).
* Re-attach by the same client_id is idempotent.
* Writer downgrades to observer → writer slot freed.
* Network drop + reconnect with ``last_event_id`` → no duplicates.
* Buffer eviction → caller sees EVENTS_EVICTED and can re-attach fresh.
* Forking duplicates state under a new session id.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from llm_code.server.proto import (
    JsonRpcErrorCode,
    JsonRpcRequest,
    SessionRole,
)
from llm_code.server.server import EVENT_BUFFER_SIZE, SessionManager
from llm_code.server.tokens import TokenStore


@pytest.fixture
def store(tmp_path: Path) -> TokenStore:
    return TokenStore(tmp_path / "tokens.db")


@pytest.fixture
def manager(store: TokenStore) -> SessionManager:
    return SessionManager(tokens=store)


@pytest.fixture
async def session(manager: SessionManager, store: TokenStore):
    admin = store.grant("*", SessionRole.WRITER)
    resp = await manager.dispatch(
        token=admin.token,
        request=JsonRpcRequest(id=1, method="session.create"),
        client_id="admin",
    )
    return resp.result["session_id"]


# ── shared helpers ───────────────────────────────────────────────────


async def _attach(
    manager: SessionManager,
    store: TokenStore,
    session_id: str,
    client_id: str,
    role: SessionRole,
):
    bearer = store.grant(session_id, role)
    resp = await manager.dispatch(
        token=bearer.token,
        request=JsonRpcRequest(
            id=10, method="session.attach",
            params={"session_id": session_id, "role": role.value},
        ),
        client_id=client_id,
    )
    return bearer, resp


# ── tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_two_clients_attach_and_observe(
    manager: SessionManager, store: TokenStore, session: str
) -> None:
    writer_bearer, writer_attach = await _attach(
        manager, store, session, "writer1", SessionRole.WRITER
    )
    obs_bearer, obs_attach = await _attach(
        manager, store, session, "obs1", SessionRole.OBSERVER
    )
    assert writer_attach.error is None
    assert obs_attach.error is None

    send = await manager.dispatch(
        token=writer_bearer.token,
        request=JsonRpcRequest(
            id=11, method="session.send",
            params={"session_id": session, "text": "hello"},
        ),
        client_id="writer1",
    )
    assert send.error is None

    sess = manager.get(session)
    assert sess is not None
    writer_q = sess.observers["writer1"].queue
    obs_q = sess.observers["obs1"].queue
    w_event = await asyncio.wait_for(writer_q.get(), timeout=1.0)
    o_event = await asyncio.wait_for(obs_q.get(), timeout=1.0)
    assert w_event.payload == {"type": "user_message", "text": "hello"}
    assert o_event.payload == {"type": "user_message", "text": "hello"}
    assert w_event.event_id == o_event.event_id == 1


@pytest.mark.asyncio
async def test_second_writer_attach_returns_conflict(
    manager: SessionManager, store: TokenStore, session: str
) -> None:
    await _attach(manager, store, session, "w1", SessionRole.WRITER)
    _, resp = await _attach(manager, store, session, "w2", SessionRole.WRITER)
    assert resp.error is not None
    assert resp.error.code == JsonRpcErrorCode.WRITER_CONFLICT.value


@pytest.mark.asyncio
async def test_same_client_re_attach_is_idempotent(
    manager: SessionManager, store: TokenStore, session: str
) -> None:
    _, first = await _attach(manager, store, session, "w1", SessionRole.WRITER)
    _, second = await _attach(manager, store, session, "w1", SessionRole.WRITER)
    assert first.error is None
    assert second.error is None


@pytest.mark.asyncio
async def test_writer_downgrade_to_observer_releases_slot(
    manager: SessionManager, store: TokenStore, session: str
) -> None:
    await _attach(manager, store, session, "w1", SessionRole.WRITER)
    _, downgrade = await _attach(
        manager, store, session, "w1", SessionRole.OBSERVER
    )
    assert downgrade.error is None
    sess = manager.get(session)
    assert sess.writer_client_id is None
    # Now another writer should succeed
    _, reattach = await _attach(manager, store, session, "w2", SessionRole.WRITER)
    assert reattach.error is None


@pytest.mark.asyncio
async def test_reconnect_with_last_event_id_replays(
    manager: SessionManager, store: TokenStore, session: str
) -> None:
    writer_bearer, _ = await _attach(
        manager, store, session, "w1", SessionRole.WRITER
    )
    # Send three messages.
    for i in range(3):
        await manager.dispatch(
            token=writer_bearer.token,
            request=JsonRpcRequest(
                id=20 + i, method="session.send",
                params={"session_id": session, "text": f"m{i}"},
            ),
            client_id="w1",
        )
    # Observer arrives after the fact and asks for everything from id=1.
    obs_bearer = store.grant(session, SessionRole.OBSERVER)
    resp = await manager.dispatch(
        token=obs_bearer.token,
        request=JsonRpcRequest(
            id=99, method="session.attach",
            params={
                "session_id": session,
                "role": "observer",
                "last_event_id": 0,
            },
        ),
        client_id="obs1",
    )
    assert resp.error is None
    # Ask for events after id=1; we should get only id=2,3
    second_resp = await manager.dispatch(
        token=obs_bearer.token,
        request=JsonRpcRequest(
            id=100, method="session.attach",
            params={
                "session_id": session,
                "role": "observer",
                "last_event_id": 1,
            },
        ),
        client_id="obs2",
    )
    assert second_resp.error is None
    replayed_ids = [e["event_id"] for e in second_resp.result["replayed"]]
    assert replayed_ids == [2, 3]


@pytest.mark.asyncio
async def test_reconnect_after_buffer_evicted(
    manager: SessionManager, store: TokenStore, session: str
) -> None:
    writer_bearer, _ = await _attach(
        manager, store, session, "w1", SessionRole.WRITER
    )
    # Manually emit ``EVENT_BUFFER_SIZE + 5`` events to push past the cap.
    for _ in range(EVENT_BUFFER_SIZE + 5):
        await manager.emit_event(session, {"type": "tick"})
    obs_bearer = store.grant(session, SessionRole.OBSERVER)
    resp = await manager.dispatch(
        token=obs_bearer.token,
        request=JsonRpcRequest(
            id=200, method="session.attach",
            params={
                "session_id": session,
                "role": "observer",
                "last_event_id": 1,  # very old cursor → evicted
            },
        ),
        client_id="obs1",
    )
    assert resp.error is not None
    assert resp.error.code == JsonRpcErrorCode.EVENTS_EVICTED.value


@pytest.mark.asyncio
async def test_fork_creates_new_session_id(
    manager: SessionManager, store: TokenStore, session: str
) -> None:
    writer_bearer, _ = await _attach(
        manager, store, session, "w1", SessionRole.WRITER
    )
    resp = await manager.dispatch(
        token=writer_bearer.token,
        request=JsonRpcRequest(
            id=300, method="session.fork",
            params={"session_id": session},
        ),
        client_id="w1",
    )
    assert resp.error is None
    forked_id = resp.result["session_id"]
    assert forked_id != session
    forked = manager.get(forked_id)
    assert forked is not None
    assert forked.writer_client_id is None
    assert forked.observers == {}


@pytest.mark.asyncio
async def test_detach_clears_writer_slot(
    manager: SessionManager, store: TokenStore, session: str
) -> None:
    writer_bearer, _ = await _attach(
        manager, store, session, "w1", SessionRole.WRITER
    )
    sess = manager.get(session)
    assert sess.writer_client_id == "w1"
    resp = await manager.dispatch(
        token=writer_bearer.token,
        request=JsonRpcRequest(
            id=400, method="session.detach",
            params={"session_id": session},
        ),
        client_id="w1",
    )
    assert resp.error is None
    assert sess.writer_client_id is None
    assert "w1" not in sess.observers


@pytest.mark.asyncio
async def test_50_concurrent_observers(
    manager: SessionManager, store: TokenStore, session: str
) -> None:
    """50 observers attach + receive an event each."""
    writer_bearer, _ = await _attach(
        manager, store, session, "writer", SessionRole.WRITER
    )
    handles = []
    for i in range(50):
        bearer = store.grant(session, SessionRole.OBSERVER)
        resp = await manager.dispatch(
            token=bearer.token,
            request=JsonRpcRequest(
                id=500 + i, method="session.attach",
                params={"session_id": session, "role": "observer"},
            ),
            client_id=f"obs{i}",
        )
        assert resp.error is None
        handles.append(f"obs{i}")
    await manager.dispatch(
        token=writer_bearer.token,
        request=JsonRpcRequest(
            id=600, method="session.send",
            params={"session_id": session, "text": "broadcast"},
        ),
        client_id="writer",
    )
    sess = manager.get(session)
    for client_id in handles:
        q = sess.observers[client_id].queue
        evt = await asyncio.wait_for(q.get(), timeout=1.0)
        assert evt.payload == {"type": "user_message", "text": "broadcast"}


@pytest.mark.asyncio
async def test_cross_session_token_rejected(
    manager: SessionManager, store: TokenStore, session: str
) -> None:
    """A token bound to session A cannot attach to session B."""
    other = await manager.create_session()
    bearer = store.grant(session, SessionRole.WRITER)
    resp = await manager.dispatch(
        token=bearer.token,
        request=JsonRpcRequest(
            id=700, method="session.attach",
            params={"session_id": other.session_id, "role": "writer"},
        ),
        client_id="cross",
    )
    assert resp.error is not None
    assert resp.error.code == JsonRpcErrorCode.UNAUTHORIZED.value


@pytest.mark.asyncio
async def test_observer_token_cannot_attach_as_writer(
    manager: SessionManager, store: TokenStore, session: str
) -> None:
    bearer = store.grant(session, SessionRole.OBSERVER)
    resp = await manager.dispatch(
        token=bearer.token,
        request=JsonRpcRequest(
            id=800, method="session.attach",
            params={"session_id": session, "role": "writer"},
        ),
        client_id="obs",
    )
    assert resp.error is not None
    assert resp.error.code == JsonRpcErrorCode.UNAUTHORIZED.value
