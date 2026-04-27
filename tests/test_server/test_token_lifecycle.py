"""Token lifecycle for v16 M9.

Covers grant → validate → revoke → re-validate semantics, expiry,
fingerprint logging discipline, and survival across a store reopen
(SQLite WAL pinning).
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from llm_code.server.proto import JsonRpcErrorCode, JsonRpcRequest, SessionRole
from llm_code.server.server import SessionManager
from llm_code.server.tokens import (
    TokenStore,
    TokenValidationError,
    token_fingerprint,
)


@pytest.fixture
def store(tmp_path: Path) -> TokenStore:
    return TokenStore(tmp_path / "tokens.db")


def test_grant_validate_round_trip(store: TokenStore) -> None:
    bearer = store.grant("sess1", SessionRole.WRITER, ttl=60)
    decoded = store.validate(bearer.token)
    assert decoded.session_id == "sess1"
    assert decoded.role == SessionRole.WRITER
    assert decoded.expires_at == bearer.expires_at


def test_revoke_invalidates_token(store: TokenStore) -> None:
    bearer = store.grant("sess1", SessionRole.WRITER, ttl=60)
    assert store.revoke(bearer.token) is True
    with pytest.raises(TokenValidationError):
        store.validate(bearer.token)


def test_revoke_unknown_token_returns_false(store: TokenStore) -> None:
    assert store.revoke("v1.bogus.bogus") is False


def test_expired_token_fails_validation(store: TokenStore) -> None:
    bearer = store.grant("sess1", SessionRole.WRITER, ttl=0.001)
    time.sleep(0.05)
    with pytest.raises(TokenValidationError):
        store.validate(bearer.token)


def test_token_fingerprint_is_8_hex(store: TokenStore) -> None:
    bearer = store.grant("sess1", SessionRole.WRITER)
    fp = token_fingerprint(bearer.token)
    assert len(fp) == 8
    assert all(c in "0123456789abcdef" for c in fp)


def test_tampered_signature_fails(store: TokenStore) -> None:
    bearer = store.grant("sess1", SessionRole.WRITER)
    parts = bearer.token.split(".")
    tampered = ".".join(parts[:-1]) + "." + ("A" * len(parts[-1]))
    with pytest.raises(TokenValidationError):
        store.validate(tampered)


def test_malformed_token_fails(store: TokenStore) -> None:
    with pytest.raises(TokenValidationError):
        store.validate("not-a-token")


def test_list_tokens_returns_fingerprints(store: TokenStore) -> None:
    store.grant("a", SessionRole.WRITER)
    store.grant("b", SessionRole.OBSERVER)
    rows = store.list_tokens()
    assert len(rows) == 2
    assert all("fingerprint" in r and len(r["fingerprint"]) == 8 for r in rows)


def test_store_survives_reopen(tmp_path: Path) -> None:
    db = tmp_path / "tokens.db"
    s1 = TokenStore(db)
    bearer = s1.grant("sess1", SessionRole.WRITER, ttl=60)
    s1.close()
    s2 = TokenStore(db)
    decoded = s2.validate(bearer.token)
    assert decoded.session_id == "sess1"


@pytest.mark.asyncio
async def test_revoked_token_blocks_subsequent_dispatch(
    store: TokenStore,
) -> None:
    """Revocation is immediate — next dispatch returns UNAUTHORIZED."""
    manager = SessionManager(tokens=store)
    bearer = store.grant("*", SessionRole.WRITER)
    create = await manager.dispatch(
        token=bearer.token,
        request=JsonRpcRequest(id=1, method="session.create"),
        client_id="c1",
    )
    assert create.error is None
    store.revoke(bearer.token)
    second = await manager.dispatch(
        token=bearer.token,
        request=JsonRpcRequest(id=2, method="session.create"),
        client_id="c1",
    )
    assert second.error is not None
    assert second.error.code == JsonRpcErrorCode.UNAUTHORIZED.value


def test_purge_expired_drops_old_rows(store: TokenStore) -> None:
    store.grant("a", SessionRole.WRITER, ttl=0.001)
    time.sleep(0.05)
    store.grant("b", SessionRole.WRITER, ttl=600)
    purged = store.purge_expired()
    assert purged == 1
    rows = store.list_tokens()
    assert len(rows) == 1
    assert rows[0]["session_id"] == "b"
