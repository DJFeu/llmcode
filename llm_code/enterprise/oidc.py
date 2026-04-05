"""OIDC authentication provider with PKCE flow."""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
from base64 import urlsafe_b64encode
from dataclasses import dataclass
from pathlib import Path

import httpx

from llm_code.enterprise.auth import AuthIdentity, AuthProvider

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OIDCConfig:
    issuer: str
    client_id: str
    client_secret: str = ""
    scopes: tuple[str, ...] = ("openid", "email", "profile")
    redirect_port: int = 9877


class OIDCProvider(AuthProvider):
    def __init__(self, config: OIDCConfig, token_dir: Path | None = None) -> None:
        self._config = config
        self._token_dir = token_dir or Path.home() / ".llm-code" / "auth"
        self._token_path = self._token_dir / "oidc_tokens.json"
        self._endpoints: dict[str, str] | None = None

    async def _discover(self) -> dict[str, str]:
        if self._endpoints is not None:
            return self._endpoints
        url = f"{self._config.issuer.rstrip('/')}/.well-known/openid-configuration"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            self._endpoints = resp.json()
            return self._endpoints

    @staticmethod
    def _generate_pkce() -> tuple[str, str]:
        verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(verifier.encode()).digest()
        challenge = urlsafe_b64encode(digest).rstrip(b"=").decode()
        return verifier, challenge

    def _save_tokens(self, tokens: dict) -> None:
        self._token_dir.mkdir(parents=True, exist_ok=True)
        self._token_path.write_text(json.dumps(tokens), encoding="utf-8")

    def _load_tokens(self) -> dict | None:
        if not self._token_path.exists():
            return None
        try:
            return json.loads(self._token_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    async def authenticate(self) -> AuthIdentity:
        await self._discover()
        raise NotImplementedError(
            "Full OIDC PKCE flow requires browser interaction. "
            "Use 'llm-code auth login' command."
        )

    async def refresh(self) -> AuthIdentity | None:
        tokens = self._load_tokens()
        if not tokens or "refresh_token" not in tokens:
            return None
        endpoints = await self._discover()
        token_url = endpoints.get("token_endpoint", "")
        async with httpx.AsyncClient() as client:
            resp = await client.post(token_url, data={
                "grant_type": "refresh_token",
                "client_id": self._config.client_id,
                "refresh_token": tokens["refresh_token"],
            })
            if resp.status_code != 200:
                return None
            new_tokens = resp.json()
            self._save_tokens(new_tokens)
            return AuthIdentity(
                user_id=new_tokens.get("sub", ""),
                email=new_tokens.get("email", ""),
                display_name=new_tokens.get("name", ""),
            )

    async def revoke(self) -> None:
        if self._token_path.exists():
            self._token_path.unlink()
