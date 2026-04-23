"""Bearer-token auth for hayhooks HTTP transports.

Security invariants:
- Compare tokens in constant time via ``hmac.compare_digest`` (no timing leak).
- Reject requests with no / malformed Authorization header (401).
- Never log the token itself — only a truncated SHA256 fingerprint.
- Missing environment variable is a hard startup failure; we never default
  to a known/empty token.
"""
from __future__ import annotations

import hashlib
import hmac
import os

from llm_code.hayhooks.errors import InvalidTokenError, MissingTokenError


def _default_token_env() -> str:
    """Return the canonical env var name for the bearer token.

    Kept as a function rather than a constant so tests / config overrides
    can monkey-patch ``HayhooksConfig().auth_token_env`` without re-
    reading an import-time constant.
    """
    return "LLMCODE_HAYHOOKS_TOKEN"


def expected_token(env_var: str | None = None) -> str:
    """Return the configured bearer token or raise ``RuntimeError``.

    The env var name defaults to ``LLMCODE_HAYHOOKS_TOKEN`` but can be
    overridden by ``HayhooksConfig.auth_token_env``.
    """
    name = env_var or _default_token_env()
    tok = os.environ.get(name, "")
    if not tok:
        raise RuntimeError(
            f"{name} is not set; cannot serve hayhooks HTTP transport"
        )
    return tok


def fingerprint(token: str) -> str:
    """Truncated SHA256 fingerprint — safe to log, never the raw token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def parse_bearer(authorization: str | None) -> str:
    """Extract the token portion of a ``Bearer <token>`` header.

    Raises :class:`MissingTokenError` when the header is absent and
    :class:`InvalidTokenError` when the syntax is malformed.
    """
    if not authorization:
        raise MissingTokenError()
    parts = authorization.split(maxsplit=1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        raise InvalidTokenError()
    return parts[1]


def verify_token(
    authorization: str | None,
    env_var: str | None = None,
) -> str:
    """Verify an Authorization header.

    Returns a fingerprint suitable for logging. Raises
    :class:`InvalidTokenError` on mismatch and :class:`MissingTokenError`
    when the header is missing.
    """
    submitted = parse_bearer(authorization)
    expected = expected_token(env_var)
    if not hmac.compare_digest(submitted, expected):
        raise InvalidTokenError()
    return fingerprint(submitted)


# FastAPI-specific wrapper — only loaded when FastAPI is installed.
try:  # pragma: no cover — exercised in tests that require fastapi
    from fastapi import Header, HTTPException, status

    async def require_bearer(
        authorization: str | None = Header(default=None),
    ) -> str:
        """FastAPI dependency — verifies bearer token; returns fingerprint."""
        try:
            return verify_token(authorization)
        except (MissingTokenError, InvalidTokenError) as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=exc.message,
            ) from exc
except ImportError:  # pragma: no cover — fastapi not installed
    def require_bearer(authorization: str | None = None) -> str:  # type: ignore[misc]
        raise RuntimeError(
            "fastapi is required for require_bearer; "
            "install llmcode[hayhooks] first"
        )
