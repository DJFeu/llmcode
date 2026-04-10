"""Enterprise features — auth, RBAC, audit.

Merged from the former ``llm_code.enterprise`` package into a single
module under ``llm_code.runtime`` to reduce top-level package count.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import secrets
from abc import ABC, abstractmethod
from base64 import urlsafe_b64encode
from dataclasses import dataclass, field
from pathlib import Path

import httpx

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# auth — Authentication provider abstraction and identity model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthIdentity:
    """Represents an authenticated user."""
    user_id: str
    email: str
    display_name: str
    groups: tuple[str, ...] = ()
    raw_claims: dict = field(default_factory=dict)


class AuthProvider(ABC):
    @abstractmethod
    async def authenticate(self) -> AuthIdentity: ...

    @abstractmethod
    async def refresh(self) -> AuthIdentity | None: ...

    @abstractmethod
    async def revoke(self) -> None: ...


# ---------------------------------------------------------------------------
# oidc — OIDC authentication provider with PKCE flow
# ---------------------------------------------------------------------------


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
        self._token_dir = token_dir or Path.home() / ".llmcode" / "auth"
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


# ---------------------------------------------------------------------------
# rbac — Role-based access control engine
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Role:
    name: str
    permissions: frozenset[str]
    tool_allow: tuple[str, ...] = ()
    tool_deny: tuple[str, ...] = ()


DEFAULT_ROLES: dict[str, Role] = {
    "admin": Role("admin", frozenset({"*"})),
    "developer": Role(
        "developer",
        frozenset({"tool:*", "swarm:create", "session:*", "skill:*"}),
        tool_deny=("tool:bash:rm -rf *",),
    ),
    "viewer": Role(
        "viewer",
        frozenset({"tool:read", "tool:glob", "tool:grep", "session:read"}),
    ),
}


class RBACEngine:
    def __init__(self, group_role_mapping: dict[str, str], custom_roles: dict[str, Role] | None = None) -> None:
        self._group_role_mapping = group_role_mapping
        self._roles = {**DEFAULT_ROLES, **(custom_roles or {})}

    def _get_roles(self, identity: AuthIdentity | None) -> list[Role]:
        if identity is None:
            return [self._roles["admin"]]
        roles = []
        for group in identity.groups:
            role_name = self._group_role_mapping.get(group)
            if role_name and role_name in self._roles:
                roles.append(self._roles[role_name])
        return roles

    def is_allowed(self, identity: AuthIdentity | None, permission: str) -> bool:
        roles = self._get_roles(identity)
        if not roles:
            return False
        for role in roles:
            if "*" in role.permissions:
                return True
            for perm in role.permissions:
                if perm == permission or (perm.endswith(":*") and permission.startswith(perm[:-1])):
                    return True
        return False

    def is_denied_by_pattern(self, identity: AuthIdentity | None, action: str) -> bool:
        roles = self._get_roles(identity)
        for role in roles:
            for pattern in role.tool_deny:
                if fnmatch.fnmatch(action, pattern):
                    return True
        return False


# ---------------------------------------------------------------------------
# audit — Audit logging (JSONL file logger with composite support)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditEvent:
    timestamp: str
    event_type: str
    user_id: str
    tool_name: str = ""
    action: str = ""
    outcome: str = ""
    metadata: dict = field(default_factory=dict)


class AuditLogger(ABC):
    @abstractmethod
    async def log(self, event: AuditEvent) -> None: ...


class FileAuditLogger(AuditLogger):
    def __init__(self, audit_dir: Path) -> None:
        self._audit_dir = audit_dir

    async def log(self, event: AuditEvent) -> None:
        self._audit_dir.mkdir(parents=True, exist_ok=True)
        date_str = event.timestamp[:10]
        path = self._audit_dir / f"{date_str}.jsonl"
        line = json.dumps({
            "timestamp": event.timestamp,
            "event_type": event.event_type,
            "user_id": event.user_id,
            "tool_name": event.tool_name,
            "action": event.action,
            "outcome": event.outcome,
            "metadata": event.metadata,
        })
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


class CompositeAuditLogger(AuditLogger):
    def __init__(self, loggers: list[AuditLogger]) -> None:
        self._loggers = loggers

    async def log(self, event: AuditEvent) -> None:
        for logger in self._loggers:
            try:
                await logger.log(event)
            except Exception as exc:
                _log.warning("Audit logger failed: %s", exc)
