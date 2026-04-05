"""Role-based access control engine."""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from llm_code.enterprise.auth import AuthIdentity


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
