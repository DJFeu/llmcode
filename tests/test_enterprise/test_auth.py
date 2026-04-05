"""Tests for enterprise auth abstractions."""
from __future__ import annotations

import pytest

from llm_code.enterprise.auth import AuthIdentity, AuthProvider


class TestAuthIdentity:
    def test_create_minimal(self) -> None:
        identity = AuthIdentity(user_id="u1", email="a@b.com", display_name="Alice")
        assert identity.user_id == "u1"
        assert identity.groups == ()
        assert identity.raw_claims == {}

    def test_create_with_groups(self) -> None:
        identity = AuthIdentity(
            user_id="u1", email="a@b.com", display_name="Alice",
            groups=("admin", "dev"),
        )
        assert identity.groups == ("admin", "dev")

    def test_frozen(self) -> None:
        identity = AuthIdentity(user_id="u1", email="a@b.com", display_name="Alice")
        with pytest.raises(AttributeError):
            identity.user_id = "u2"


class TestAuthProviderABC:
    def test_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            AuthProvider()

    def test_subclass_must_implement(self) -> None:
        class Bad(AuthProvider):
            pass
        with pytest.raises(TypeError):
            Bad()

    def test_valid_subclass(self) -> None:
        class Good(AuthProvider):
            async def authenticate(self) -> AuthIdentity:
                return AuthIdentity(user_id="x", email="x@x.com", display_name="X")
            async def refresh(self) -> AuthIdentity | None:
                return None
            async def revoke(self) -> None:
                pass
        provider = Good()
        assert provider is not None
