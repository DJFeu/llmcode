"""Enterprise-specific frozen dataclasses extracted from config.py."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class EnterpriseAuthConfig:
    provider: str = ""  # "" | "none" | "oidc"
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_scopes: tuple[str, ...] = ("openid", "email", "profile")
    oidc_redirect_port: int = 9877


@dataclass(frozen=True)
class EnterpriseRBACConfig:
    group_role_mapping: dict[str, str] = field(default_factory=dict)
    custom_roles: dict = field(default_factory=dict)


@dataclass(frozen=True)
class EnterpriseAuditConfig:
    retention_days: int = 90


@dataclass(frozen=True)
class EnterpriseConfig:
    auth: EnterpriseAuthConfig = field(default_factory=EnterpriseAuthConfig)
    rbac: EnterpriseRBACConfig = field(default_factory=EnterpriseRBACConfig)
    audit: EnterpriseAuditConfig = field(default_factory=EnterpriseAuditConfig)
