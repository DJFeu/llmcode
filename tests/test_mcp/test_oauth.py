"""Tests for OAuth 2.0 + PKCE (Feature 4)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from llm_code.mcp.oauth import OAuthClient, OAuthToken


# ---------------------------------------------------------------------------
# OAuthToken unit tests
# ---------------------------------------------------------------------------


class TestOAuthToken:
    def test_creation_with_required_field(self) -> None:
        token = OAuthToken(access_token="abc123")
        assert token.access_token == "abc123"

    def test_default_token_type(self) -> None:
        token = OAuthToken(access_token="abc123")
        assert token.token_type == "Bearer"

    def test_default_expires_at_is_zero(self) -> None:
        token = OAuthToken(access_token="abc123")
        assert token.expires_at == 0.0

    def test_default_refresh_token_is_empty(self) -> None:
        token = OAuthToken(access_token="abc123")
        assert token.refresh_token == ""

    def test_default_scope_is_empty(self) -> None:
        token = OAuthToken(access_token="abc123")
        assert token.scope == ""

    def test_is_expired_when_expires_at_is_zero(self) -> None:
        token = OAuthToken(access_token="abc123", expires_at=0.0)
        assert token.is_expired is True

    def test_is_expired_when_past_expiry(self) -> None:
        past = time.time() - 120
        token = OAuthToken(access_token="abc123", expires_at=past)
        assert token.is_expired is True

    def test_is_not_expired_when_well_within_expiry(self) -> None:
        future = time.time() + 3600
        token = OAuthToken(access_token="abc123", expires_at=future)
        assert token.is_expired is False

    def test_is_expired_within_60s_buffer(self) -> None:
        # Within the 60s safety buffer should be considered expired
        near_future = time.time() + 30
        token = OAuthToken(access_token="abc123", expires_at=near_future)
        assert token.is_expired is True

    def test_to_dict_roundtrip(self) -> None:
        original = OAuthToken(
            access_token="tok",
            token_type="Bearer",
            expires_at=9999.0,
            refresh_token="ref",
            scope="read write",
        )
        d = original.to_dict()
        assert d["access_token"] == "tok"
        assert d["token_type"] == "Bearer"
        assert d["expires_at"] == 9999.0
        assert d["refresh_token"] == "ref"
        assert d["scope"] == "read write"

    def test_from_dict_roundtrip(self) -> None:
        data = {
            "access_token": "tok",
            "token_type": "Bearer",
            "expires_at": 9999.0,
            "refresh_token": "ref",
            "scope": "read write",
        }
        token = OAuthToken.from_dict(data)
        assert token.access_token == "tok"
        assert token.token_type == "Bearer"
        assert token.expires_at == 9999.0
        assert token.refresh_token == "ref"
        assert token.scope == "read write"

    def test_from_dict_ignores_unknown_keys(self) -> None:
        data = {
            "access_token": "tok",
            "unknown_key": "ignored",
        }
        token = OAuthToken.from_dict(data)
        assert token.access_token == "tok"

    def test_from_dict_partial_fields(self) -> None:
        """from_dict should work with only access_token provided."""
        token = OAuthToken.from_dict({"access_token": "minimal"})
        assert token.access_token == "minimal"
        assert token.token_type == "Bearer"


# ---------------------------------------------------------------------------
# OAuthClient token persistence tests
# ---------------------------------------------------------------------------


class TestOAuthClientTokenPersistence:
    def _make_client(self, tmp_path: Path) -> OAuthClient:
        client = OAuthClient(
            client_id="test-client",
            authorization_url="https://example.com/auth",
            token_url="https://example.com/token",
        )
        # Override token dir to use tmp_path so tests don't touch ~/.llm-code
        client._token_dir = tmp_path / "tokens"
        client._token_dir.mkdir(parents=True, exist_ok=True)
        return client

    def test_save_and_load_token(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path)
        token = OAuthToken(
            access_token="saved_token",
            expires_at=time.time() + 3600,
        )
        client._save_token("my-server", token)
        loaded = client._load_token("my-server")
        assert loaded is not None
        assert loaded.access_token == "saved_token"

    def test_load_returns_none_when_no_file(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path)
        result = client._load_token("nonexistent-server")
        assert result is None

    def test_load_returns_none_on_corrupt_file(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path)
        corrupt = client._token_dir / "bad-server.json"
        corrupt.write_text("not json {{{")
        result = client._load_token("bad-server")
        assert result is None

    def test_get_token_returns_valid_token(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path)
        token = OAuthToken(
            access_token="valid",
            expires_at=time.time() + 3600,
        )
        client._save_token("srv", token)
        result = client.get_token("srv")
        assert result is not None
        assert result.access_token == "valid"

    def test_get_token_returns_none_for_expired_no_refresh(
        self, tmp_path: Path
    ) -> None:
        client = self._make_client(tmp_path)
        expired = OAuthToken(
            access_token="expired",
            expires_at=time.time() - 120,
            refresh_token="",
        )
        client._save_token("srv", expired)
        result = client.get_token("srv")
        assert result is None

    def test_get_token_refreshes_expired_token(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path)
        expired = OAuthToken(
            access_token="old",
            expires_at=time.time() - 120,
            refresh_token="refresh_tok",
        )
        client._save_token("srv", expired)

        new_token = OAuthToken(
            access_token="new",
            expires_at=time.time() + 3600,
            refresh_token="refresh_tok",
        )
        with patch.object(client, "_refresh_token", return_value=new_token):
            result = client.get_token("srv")

        assert result is not None
        assert result.access_token == "new"

    def test_get_token_returns_none_if_refresh_fails(
        self, tmp_path: Path
    ) -> None:
        client = self._make_client(tmp_path)
        expired = OAuthToken(
            access_token="old",
            expires_at=time.time() - 120,
            refresh_token="refresh_tok",
        )
        client._save_token("srv", expired)

        with patch.object(client, "_refresh_token", return_value=None):
            result = client.get_token("srv")

        assert result is None


# ---------------------------------------------------------------------------
# OAuthClient constructor / config tests
# ---------------------------------------------------------------------------


class TestOAuthClientConfig:
    def test_default_redirect_uri(self) -> None:
        client = OAuthClient(
            client_id="id",
            authorization_url="https://a.com/auth",
            token_url="https://a.com/token",
        )
        assert client._redirect_uri == "http://localhost:9876/callback"

    def test_custom_redirect_uri(self) -> None:
        client = OAuthClient(
            client_id="id",
            authorization_url="https://a.com/auth",
            token_url="https://a.com/token",
            redirect_uri="http://localhost:1234/cb",
        )
        assert client._redirect_uri == "http://localhost:1234/cb"

    def test_scope_stored(self) -> None:
        client = OAuthClient(
            client_id="id",
            authorization_url="https://a.com/auth",
            token_url="https://a.com/token",
            scope="read write",
        )
        assert client._scope == "read write"

    def test_token_dir_created(self, tmp_path: Path) -> None:
        """Token dir should be created on instantiation (uses real home here)."""
        client = OAuthClient(
            client_id="id",
            authorization_url="https://a.com/auth",
            token_url="https://a.com/token",
        )
        assert client._token_dir.exists()
