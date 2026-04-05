"""Tests for OIDC authentication provider."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_code.enterprise.oidc import OIDCConfig, OIDCProvider


class TestOIDCConfig:
    def test_create_minimal(self) -> None:
        config = OIDCConfig(issuer="https://accounts.google.com", client_id="abc")
        assert config.issuer == "https://accounts.google.com"
        assert config.client_secret == ""
        assert config.scopes == ("openid", "email", "profile")
        assert config.redirect_port == 9877

    def test_frozen(self) -> None:
        config = OIDCConfig(issuer="x", client_id="y")
        with pytest.raises(AttributeError):
            config.issuer = "z"


class TestOIDCProviderTokenStorage:
    def test_token_path(self, tmp_path) -> None:
        config = OIDCConfig(issuer="https://example.com", client_id="abc")
        provider = OIDCProvider(config, token_dir=tmp_path)
        assert provider._token_path == tmp_path / "oidc_tokens.json"

    def test_save_and_load_tokens(self, tmp_path) -> None:
        config = OIDCConfig(issuer="https://example.com", client_id="abc")
        provider = OIDCProvider(config, token_dir=tmp_path)
        provider._save_tokens({"access_token": "abc", "refresh_token": "xyz"})
        loaded = provider._load_tokens()
        assert loaded is not None
        assert loaded["access_token"] == "abc"

    def test_load_missing_tokens(self, tmp_path) -> None:
        config = OIDCConfig(issuer="https://example.com", client_id="abc")
        provider = OIDCProvider(config, token_dir=tmp_path)
        assert provider._load_tokens() is None

    @pytest.mark.asyncio
    async def test_revoke_deletes_tokens(self, tmp_path) -> None:
        config = OIDCConfig(issuer="https://example.com", client_id="abc")
        provider = OIDCProvider(config, token_dir=tmp_path)
        provider._save_tokens({"access_token": "abc"})
        assert provider._token_path.exists()
        await provider.revoke()
        assert not provider._token_path.exists()


class TestOIDCProviderPKCE:
    def test_generate_pkce(self) -> None:
        verifier, challenge = OIDCProvider._generate_pkce()
        assert len(verifier) > 0
        assert len(challenge) > 0
        assert verifier != challenge
