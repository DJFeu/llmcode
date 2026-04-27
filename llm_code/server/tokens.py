"""HMAC-signed bearer tokens with SQLite-backed validation.

The protocol tier (``server.py``) calls :meth:`TokenStore.validate`
on every method invocation — never a cached lookup, so revocation is
immediate. The store sits at ``~/.llmcode/server/tokens.db`` (SQLite
WAL) so token state survives a restart and lays the WAL groundwork
for M10's state DB.

Token shape::

    payload   = base64url(json({"session_id", "role", "expires_at",
                                 "issued_at", "nonce"}))
    signature = base64url(hmac_sha256(secret, payload))
    token     = "v1." + payload + "." + signature

Signed-JSON over opaque IDs makes the token self-describing without
extra DB lookups; the SQLite row exists so revocation is a single
``DELETE`` away from being immediate. The store also enforces TTL —
expired tokens fail validation even if their HMAC is fine.

Logging discipline: never log a full token. The convenience hash
helper :func:`token_fingerprint` returns the first 8 hex chars of
``sha256(token)`` — safe for log lines (``token=<hash[:8]>``).
"""
from __future__ import annotations

import base64
import dataclasses
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from llm_code.server.proto import SessionRole

logger = logging.getLogger(__name__)


_TOKEN_PREFIX = "v1."
_DEFAULT_TTL_SECONDS = 3600


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def token_fingerprint(token: str) -> str:
    """Return the 8-hex-char SHA-256 prefix; safe to log."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]


class TokenValidationError(Exception):
    """Raised on bad signature, malformed payload, or expired token."""


@dataclasses.dataclass(frozen=True)
class BearerToken:
    """Validated payload extracted from a bearer token."""

    token: str
    session_id: str
    role: SessionRole
    expires_at: float
    issued_at: float

    @property
    def fingerprint(self) -> str:
        return token_fingerprint(self.token)


# ── store ─────────────────────────────────────────────────────────────


class TokenStore:
    """SQLite-backed token registry with HMAC issuance + validation.

    Single-writer thread safety: SQLite + WAL handles read concurrency,
    and we serialise writes via ``threading.RLock`` so the issuer/
    validator can be shared between asyncio handlers (which bounce off
    the GIL anyway). The store opens the DB lazily so import of this
    module does not touch disk.

    The HMAC secret rotates on first run if the DB is empty: a 32-byte
    secret is generated and cached in the ``config`` table. Subsequent
    opens reuse it. Operators can pre-seed a secret via the
    ``LLMCODE_SERVER_TOKEN_SECRET`` env var (32+ ascii bytes) for
    multi-host deployments — the env value, when present, takes
    precedence over the persisted secret.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    def _ensure_open(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        with self._lock:
            if self._conn is not None:
                return self._conn
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                self._db_path,
                check_same_thread=False,
                isolation_level=None,  # autocommit; we use BEGIN explicitly
                timeout=5.0,
            )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS tokens ("
                " token TEXT PRIMARY KEY,"
                " session_id TEXT NOT NULL,"
                " role TEXT NOT NULL,"
                " expires_at REAL NOT NULL,"
                " issued_at REAL NOT NULL"
                ")"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS config ("
                " key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            self._conn = conn
            return conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ── secret resolution ────────────────────────────────────────────

    def _resolve_secret(self) -> bytes:
        env_val = os.environ.get("LLMCODE_SERVER_TOKEN_SECRET")
        if env_val and len(env_val) >= 32:
            return env_val.encode("utf-8")
        conn = self._ensure_open()
        with self._lock:
            row = conn.execute(
                "SELECT value FROM config WHERE key='hmac_secret'"
            ).fetchone()
            if row is not None:
                return _b64u_decode(row[0])
            secret = secrets.token_bytes(32)
            conn.execute(
                "INSERT INTO config (key, value) VALUES ('hmac_secret', ?)",
                (_b64u(secret),),
            )
            return secret

    # ── issuance ─────────────────────────────────────────────────────

    def grant(
        self,
        session_id: str,
        role: SessionRole,
        ttl: float = _DEFAULT_TTL_SECONDS,
    ) -> BearerToken:
        """Mint and persist a new bearer token."""
        if not session_id:
            raise ValueError("session_id is required")
        now = time.time()
        # Clamp negative / zero TTLs to a near-zero positive so the row
        # is still inserted but immediately expired — useful for tests
        # exercising the expiry path.
        expires_at = now + max(0.001, float(ttl))
        payload = {
            "session_id": session_id,
            "role": role.value,
            "issued_at": now,
            "expires_at": expires_at,
            "nonce": _b64u(secrets.token_bytes(8)),
        }
        payload_b64 = _b64u(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        secret = self._resolve_secret()
        sig = hmac.new(secret, payload_b64.encode("ascii"), hashlib.sha256).digest()
        token = f"{_TOKEN_PREFIX}{payload_b64}.{_b64u(sig)}"

        conn = self._ensure_open()
        with self._lock:
            conn.execute(
                "INSERT INTO tokens (token, session_id, role, expires_at, issued_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (token, session_id, role.value, expires_at, now),
            )
        logger.info(
            "tokens.grant session_id=%s role=%s token=%s",
            session_id,
            role.value,
            token_fingerprint(token),
        )
        return BearerToken(
            token=token,
            session_id=session_id,
            role=role,
            expires_at=expires_at,
            issued_at=now,
        )

    # ── validation ───────────────────────────────────────────────────

    def validate(self, token: str) -> BearerToken:
        """Return the validated payload or raise :class:`TokenValidationError`.

        Validation steps (all must pass):

        1. Token starts with the version prefix and splits cleanly.
        2. HMAC signature matches.
        3. Payload decodes as JSON with the expected fields.
        4. Token row exists in the ``tokens`` table (not revoked).
        5. ``expires_at`` is still in the future.
        """
        if not token or not token.startswith(_TOKEN_PREFIX):
            raise TokenValidationError("malformed token")
        rest = token[len(_TOKEN_PREFIX):]
        try:
            payload_b64, sig_b64 = rest.split(".", 1)
        except ValueError as exc:
            raise TokenValidationError("malformed token") from exc

        secret = self._resolve_secret()
        expected_sig = hmac.new(
            secret, payload_b64.encode("ascii"), hashlib.sha256
        ).digest()
        try:
            actual_sig = _b64u_decode(sig_b64)
        except Exception as exc:
            raise TokenValidationError("bad signature encoding") from exc
        if not hmac.compare_digest(expected_sig, actual_sig):
            raise TokenValidationError("bad signature")

        try:
            payload = json.loads(_b64u_decode(payload_b64))
        except Exception as exc:
            raise TokenValidationError("malformed payload") from exc

        try:
            session_id = str(payload["session_id"])
            role = SessionRole(payload["role"])
            expires_at = float(payload["expires_at"])
            issued_at = float(payload["issued_at"])
        except (KeyError, ValueError) as exc:
            raise TokenValidationError("missing payload fields") from exc

        # Revocation check — DB-backed, never cached.
        conn = self._ensure_open()
        with self._lock:
            row = conn.execute(
                "SELECT expires_at FROM tokens WHERE token=?", (token,)
            ).fetchone()
        if row is None:
            raise TokenValidationError("token revoked or unknown")
        # The DB-stored expires_at wins over the payload's claim — they
        # match in normal flow, but tying validation to the DB row means
        # a row-only mutation can shorten a token's lifetime if needed.
        if float(row[0]) <= time.time():
            raise TokenValidationError("token expired")
        if expires_at <= time.time():
            raise TokenValidationError("token expired")

        return BearerToken(
            token=token,
            session_id=session_id,
            role=role,
            expires_at=expires_at,
            issued_at=issued_at,
        )

    # ── revocation + listing ─────────────────────────────────────────

    def revoke(self, token: str) -> bool:
        """Delete a token; returns True if it existed."""
        conn = self._ensure_open()
        with self._lock:
            cursor = conn.execute("DELETE FROM tokens WHERE token=?", (token,))
            removed = cursor.rowcount > 0
        if removed:
            logger.info("tokens.revoke token=%s", token_fingerprint(token))
        return removed

    def list_tokens(self) -> list[dict]:
        """Return all current tokens (full token strings INCLUDED — caller is admin)."""
        conn = self._ensure_open()
        with self._lock:
            rows = conn.execute(
                "SELECT token, session_id, role, expires_at, issued_at FROM tokens"
                " ORDER BY issued_at DESC"
            ).fetchall()
        out: list[dict] = []
        for token, session_id, role, expires_at, issued_at in rows:
            out.append({
                "token": token,
                "fingerprint": token_fingerprint(token),
                "session_id": session_id,
                "role": role,
                "expires_at": float(expires_at),
                "issued_at": float(issued_at),
            })
        return out

    def purge_expired(self) -> int:
        """Delete every token whose ``expires_at`` is past; returns count deleted."""
        conn = self._ensure_open()
        with self._lock:
            cursor = conn.execute(
                "DELETE FROM tokens WHERE expires_at <= ?", (time.time(),)
            )
            return cursor.rowcount or 0
