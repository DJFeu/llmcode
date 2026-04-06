"""Tests for RBAC integration with PermissionPolicy."""
from __future__ import annotations


from llm_code.enterprise.auth import AuthIdentity
from llm_code.enterprise.rbac import RBACEngine
from llm_code.runtime.permissions import PermissionMode, PermissionOutcome, PermissionPolicy
from llm_code.tools.base import PermissionLevel


class TestPermissionPolicyWithRBAC:
    def test_rbac_deny_overrides_mode(self) -> None:
        rbac = RBACEngine(group_role_mapping={"viewers": "viewer"})
        identity = AuthIdentity(user_id="u1", email="a@b.com", display_name="A", groups=("viewers",))
        policy = PermissionPolicy(mode=PermissionMode.FULL_ACCESS, rbac=rbac)
        result = policy.authorize("bash", PermissionLevel.FULL_ACCESS, identity=identity)
        assert result == PermissionOutcome.DENY

    def test_no_rbac_allows_normally(self) -> None:
        policy = PermissionPolicy(mode=PermissionMode.FULL_ACCESS)
        result = policy.authorize("bash", PermissionLevel.FULL_ACCESS)
        assert result == PermissionOutcome.ALLOW

    def test_rbac_none_identity_allows(self) -> None:
        rbac = RBACEngine(group_role_mapping={})
        policy = PermissionPolicy(mode=PermissionMode.FULL_ACCESS, rbac=rbac)
        result = policy.authorize("bash", PermissionLevel.FULL_ACCESS, identity=None)
        assert result == PermissionOutcome.ALLOW

    def test_rbac_admin_allows(self) -> None:
        rbac = RBACEngine(group_role_mapping={"admins": "admin"})
        identity = AuthIdentity(user_id="u1", email="a@b.com", display_name="A", groups=("admins",))
        policy = PermissionPolicy(mode=PermissionMode.FULL_ACCESS, rbac=rbac)
        result = policy.authorize("bash", PermissionLevel.FULL_ACCESS, identity=identity)
        assert result == PermissionOutcome.ALLOW

    def test_rbac_with_existing_deny_list(self) -> None:
        rbac = RBACEngine(group_role_mapping={"admins": "admin"})
        identity = AuthIdentity(user_id="u1", email="a@b.com", display_name="A", groups=("admins",))
        policy = PermissionPolicy(
            mode=PermissionMode.FULL_ACCESS,
            rbac=rbac,
            deny_tools=frozenset({"bash"}),
        )
        # deny_tools takes precedence even if RBAC allows
        result = policy.authorize("bash", PermissionLevel.FULL_ACCESS, identity=identity)
        assert result == PermissionOutcome.DENY
