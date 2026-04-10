"""Tests for RBAC engine."""
from __future__ import annotations


from llm_code.runtime.enterprise import AuthIdentity, DEFAULT_ROLES, RBACEngine, Role


class TestRole:
    def test_admin_has_wildcard(self) -> None:
        assert "*" in DEFAULT_ROLES["admin"].permissions

    def test_viewer_limited(self) -> None:
        viewer = DEFAULT_ROLES["viewer"]
        assert "tool:read" in viewer.permissions
        assert "tool:bash" not in viewer.permissions


class TestRBACEngine:
    def test_admin_allowed_everything(self) -> None:
        engine = RBACEngine(group_role_mapping={"admins": "admin"})
        identity = AuthIdentity(user_id="u1", email="a@b.com", display_name="A", groups=("admins",))
        assert engine.is_allowed(identity, "tool:bash") is True

    def test_viewer_blocked_from_edit(self) -> None:
        engine = RBACEngine(group_role_mapping={"viewers": "viewer"})
        identity = AuthIdentity(user_id="u1", email="a@b.com", display_name="A", groups=("viewers",))
        assert engine.is_allowed(identity, "tool:read") is True
        assert engine.is_allowed(identity, "tool:bash") is False

    def test_developer_allowed_tools(self) -> None:
        engine = RBACEngine(group_role_mapping={"devs": "developer"})
        identity = AuthIdentity(user_id="u1", email="a@b.com", display_name="A", groups=("devs",))
        assert engine.is_allowed(identity, "tool:bash") is True

    def test_no_matching_group_denied(self) -> None:
        engine = RBACEngine(group_role_mapping={"admins": "admin"})
        identity = AuthIdentity(user_id="u1", email="a@b.com", display_name="A", groups=("unknown",))
        assert engine.is_allowed(identity, "tool:bash") is False

    def test_no_auth_default_admin(self) -> None:
        engine = RBACEngine(group_role_mapping={})
        assert engine.is_allowed(None, "tool:bash") is True

    def test_multiple_groups_highest_wins(self) -> None:
        engine = RBACEngine(group_role_mapping={"viewers": "viewer", "devs": "developer"})
        identity = AuthIdentity(user_id="u1", email="a@b.com", display_name="A", groups=("viewers", "devs"))
        assert engine.is_allowed(identity, "tool:bash") is True

    def test_tool_deny_pattern(self) -> None:
        engine = RBACEngine(group_role_mapping={"devs": "developer"})
        identity = AuthIdentity(user_id="u1", email="a@b.com", display_name="A", groups=("devs",))
        assert engine.is_denied_by_pattern(identity, "tool:bash:rm -rf /") is True

    def test_custom_roles(self) -> None:
        custom = Role(name="ops", permissions=frozenset({"tool:bash", "swarm:create"}))
        engine = RBACEngine(group_role_mapping={"ops-team": "ops"}, custom_roles={"ops": custom})
        identity = AuthIdentity(user_id="u1", email="a@b.com", display_name="A", groups=("ops-team",))
        assert engine.is_allowed(identity, "tool:bash") is True
        assert engine.is_allowed(identity, "tool:edit") is False
