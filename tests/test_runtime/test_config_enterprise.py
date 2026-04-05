"""Tests for enterprise config integration."""
from __future__ import annotations

import pytest

from llm_code.runtime.config import (
    EnterpriseConfig,
    EnterpriseAuthConfig,
    EnterpriseRBACConfig,
    EnterpriseAuditConfig,
    RuntimeConfig,
)


class TestEnterpriseConfig:
    def test_defaults_disabled(self) -> None:
        config = EnterpriseConfig()
        assert config.auth.provider == ""
        assert config.rbac.group_role_mapping == {}
        assert config.audit.retention_days == 90

    def test_auth_config(self) -> None:
        auth = EnterpriseAuthConfig(
            provider="oidc",
            oidc_issuer="https://accounts.google.com",
            oidc_client_id="abc",
        )
        assert auth.provider == "oidc"
        assert auth.oidc_issuer == "https://accounts.google.com"

    def test_rbac_config(self) -> None:
        rbac = EnterpriseRBACConfig(group_role_mapping={"admins": "admin"})
        assert rbac.group_role_mapping == {"admins": "admin"}

    def test_audit_config(self) -> None:
        audit = EnterpriseAuditConfig(retention_days=30)
        assert audit.retention_days == 30

    def test_runtime_config_has_enterprise(self) -> None:
        rc = RuntimeConfig()
        assert isinstance(rc.enterprise, EnterpriseConfig)
        assert rc.enterprise.auth.provider == ""
