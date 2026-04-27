"""Factory + Protocol tests for the rerank package (v2.8.0 M1)."""
from __future__ import annotations

import pytest

from llm_code.tools.rerank import (
    AuthError,
    IdentityRerankBackend,
    RateLimitError,
    RerankBackend,
    RerankResult,
    create_rerank_backend,
)


class TestRerankProtocol:
    def test_identity_backend_satisfies_protocol(self) -> None:
        backend = IdentityRerankBackend()
        assert isinstance(backend, RerankBackend)
        assert backend.name == "none"


class TestRerankResult:
    def test_dataclass_is_frozen(self) -> None:
        r = RerankResult(document="x", score=0.9, original_index=0)
        with pytest.raises(Exception):
            r.score = 0.5  # type: ignore[misc] — frozen dataclass

    def test_dataclass_fields(self) -> None:
        r = RerankResult(document="hello", score=0.5, original_index=2)
        assert r.document == "hello"
        assert r.score == 0.5
        assert r.original_index == 2


class TestIdentityBackend:
    def test_identity_returns_first_top_k_unchanged(self) -> None:
        docs = ("apple", "banana", "cherry", "date", "elderberry")
        backend = IdentityRerankBackend()
        results = backend.rerank("query", docs, top_k=3)
        assert len(results) == 3
        assert [r.document for r in results] == ["apple", "banana", "cherry"]
        assert [r.original_index for r in results] == [0, 1, 2]
        # Scores are monotone-descending.
        assert results[0].score > results[1].score > results[2].score

    def test_identity_empty_documents(self) -> None:
        backend = IdentityRerankBackend()
        assert backend.rerank("query", (), top_k=5) == ()

    def test_identity_top_k_larger_than_input(self) -> None:
        backend = IdentityRerankBackend()
        results = backend.rerank("q", ("only-one",), top_k=10)
        assert len(results) == 1
        assert results[0].document == "only-one"

    def test_identity_top_k_zero(self) -> None:
        backend = IdentityRerankBackend()
        assert backend.rerank("q", ("a", "b"), top_k=0) == ()


class TestFactory:
    def test_factory_resolves_none(self) -> None:
        backend = create_rerank_backend("none")
        assert isinstance(backend, IdentityRerankBackend)
        assert backend.name == "none"

    def test_factory_resolves_cohere(self) -> None:
        backend = create_rerank_backend("cohere", api_key="test-key")
        assert backend.name == "cohere"

    def test_factory_resolves_jina(self) -> None:
        backend = create_rerank_backend("jina", api_key="")
        assert backend.name == "jina"

    def test_factory_unknown_name_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown rerank backend"):
            create_rerank_backend("not-a-backend")

    def test_factory_cohere_pulls_env_var_when_no_explicit_key(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("COHERE_API_KEY", "from-env")
        backend = create_rerank_backend("cohere")
        # Backend stores the trimmed key; private attribute access is
        # acceptable in a unit test that pins the contract.
        assert backend._api_key == "from-env"  # type: ignore[attr-defined]

    def test_factory_jina_anonymous_when_no_key(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("JINA_API_KEY", raising=False)
        backend = create_rerank_backend("jina")
        assert backend.name == "jina"
        assert backend._api_key == ""  # type: ignore[attr-defined]


class TestExceptionTypes:
    def test_rate_limit_error_distinct_from_auth(self) -> None:
        # RateLimitError and AuthError are distinct so callers can
        # branch on transient vs permanent failure modes.
        assert not issubclass(RateLimitError, AuthError)
        assert not issubclass(AuthError, RateLimitError)
