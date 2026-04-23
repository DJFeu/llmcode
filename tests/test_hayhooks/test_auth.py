"""Tests for ``llm_code.hayhooks.auth``."""
from __future__ import annotations

import logging

import pytest

from llm_code.hayhooks.auth import (
    expected_token,
    fingerprint,
    parse_bearer,
    verify_token,
)
from llm_code.hayhooks.errors import InvalidTokenError, MissingTokenError


class TestParseBearer:
    def test_missing_header_raises_missing_token(self):
        with pytest.raises(MissingTokenError):
            parse_bearer(None)

    def test_empty_header_raises_missing_token(self):
        with pytest.raises(MissingTokenError):
            parse_bearer("")

    def test_malformed_single_part_raises_invalid(self):
        with pytest.raises(InvalidTokenError):
            parse_bearer("NotBearer")

    def test_non_bearer_scheme_raises_invalid(self):
        with pytest.raises(InvalidTokenError):
            parse_bearer("Basic abcdef")

    def test_bearer_without_token_raises_invalid(self):
        with pytest.raises(InvalidTokenError):
            parse_bearer("Bearer ")

    def test_valid_bearer_returns_token(self):
        assert parse_bearer("Bearer tok-1") == "tok-1"

    def test_case_insensitive_scheme(self):
        assert parse_bearer("bearer tok-2") == "tok-2"


class TestExpectedToken:
    def test_raises_when_env_missing(self, monkeypatch):
        monkeypatch.delenv("LLMCODE_HAYHOOKS_TOKEN", raising=False)
        with pytest.raises(RuntimeError, match="LLMCODE_HAYHOOKS_TOKEN"):
            expected_token()

    def test_returns_token_when_set(self, monkeypatch):
        monkeypatch.setenv("LLMCODE_HAYHOOKS_TOKEN", "abc")
        assert expected_token() == "abc"

    def test_supports_custom_env_var(self, monkeypatch):
        monkeypatch.setenv("MY_CUSTOM_HAYHOOKS_TOKEN", "xyz")
        assert expected_token("MY_CUSTOM_HAYHOOKS_TOKEN") == "xyz"


class TestFingerprint:
    def test_is_deterministic(self):
        assert fingerprint("abc") == fingerprint("abc")

    def test_differs_between_tokens(self):
        assert fingerprint("abc") != fingerprint("xyz")

    def test_length_is_12(self):
        assert len(fingerprint("hello")) == 12

    def test_does_not_leak_token(self):
        fp = fingerprint("super-secret")
        assert "super-secret" not in fp


class TestVerifyToken:
    def test_missing_header_401(self, monkeypatch):
        monkeypatch.setenv("LLMCODE_HAYHOOKS_TOKEN", "correct")
        with pytest.raises(MissingTokenError):
            verify_token(None)

    def test_wrong_token_401(self, monkeypatch):
        monkeypatch.setenv("LLMCODE_HAYHOOKS_TOKEN", "correct")
        with pytest.raises(InvalidTokenError):
            verify_token("Bearer wrong")

    def test_malformed_header_401(self, monkeypatch):
        monkeypatch.setenv("LLMCODE_HAYHOOKS_TOKEN", "correct")
        with pytest.raises(InvalidTokenError):
            verify_token("Basic correct")

    def test_correct_token_returns_fingerprint(self, monkeypatch):
        monkeypatch.setenv("LLMCODE_HAYHOOKS_TOKEN", "correct")
        fp = verify_token("Bearer correct")
        assert fp == fingerprint("correct")

    def test_uses_constant_time_compare(self, monkeypatch):
        """Ensure ``hmac.compare_digest`` is used, not naive ``==``.

        We monkey-patch ``hmac.compare_digest`` and assert it was called.
        """
        from llm_code.hayhooks import auth as auth_mod
        calls = []

        def _fake(a, b):
            calls.append((a, b))
            return a == b

        monkeypatch.setenv("LLMCODE_HAYHOOKS_TOKEN", "xyz")
        monkeypatch.setattr(auth_mod.hmac, "compare_digest", _fake)

        verify_token("Bearer xyz")

        assert calls, "hmac.compare_digest was never invoked"
        assert calls[0][0] == "xyz"
        assert calls[0][1] == "xyz"

    def test_token_not_logged(self, monkeypatch, caplog):
        monkeypatch.setenv("LLMCODE_HAYHOOKS_TOKEN", "sup3rs3cret-123")
        with caplog.at_level(logging.DEBUG, logger="llm_code.hayhooks.auth"):
            verify_token("Bearer sup3rs3cret-123")
        combined = "\n".join(r.getMessage() for r in caplog.records)
        assert "sup3rs3cret-123" not in combined


class TestFastApiDep:
    def test_require_bearer_exists(self):
        from llm_code.hayhooks.auth import require_bearer
        assert callable(require_bearer)
