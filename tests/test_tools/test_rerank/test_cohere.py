"""Cohere rerank backend tests (v2.8.0 M1)."""
from __future__ import annotations

import json as _json

import httpx
import pytest
import respx

from llm_code.tools.rerank import AuthError, RateLimitError, RerankResult
from llm_code.tools.rerank.cohere import CohereRerankBackend

COHERE_URL = "https://api.cohere.com/v2/rerank"


class TestCohereConstruction:
    def test_backend_name(self) -> None:
        backend = CohereRerankBackend(api_key="test-key")
        assert backend.name == "cohere"

    def test_empty_key_accepted_at_construction(self) -> None:
        backend = CohereRerankBackend(api_key="")
        assert backend._api_key == ""  # type: ignore[attr-defined]


class TestCohereRerank:
    def test_empty_key_raises_auth_error_on_call(self) -> None:
        backend = CohereRerankBackend(api_key="")
        with pytest.raises(AuthError, match="COHERE_API_KEY"):
            backend.rerank("q", ("doc1",), top_k=1)

    def test_empty_documents_returns_empty_tuple(self) -> None:
        backend = CohereRerankBackend(api_key="test-key")
        # Empty docs short-circuits before any HTTP call.
        assert backend.rerank("q", (), top_k=5) == ()

    @respx.mock
    def test_successful_rerank_orders_by_score(self) -> None:
        respx.post(COHERE_URL).mock(return_value=httpx.Response(200, json={
            "results": [
                {"index": 2, "relevance_score": 0.91},
                {"index": 0, "relevance_score": 0.42},
                {"index": 1, "relevance_score": 0.18},
            ],
        }))
        backend = CohereRerankBackend(api_key="test-key")
        docs = ("first", "second", "third")
        results = backend.rerank("q", docs, top_k=3)
        assert len(results) == 3
        assert results[0].document == "third"
        assert results[0].original_index == 2
        assert results[0].score == pytest.approx(0.91)
        assert results[1].document == "first"
        assert results[2].document == "second"
        for r in results:
            assert isinstance(r, RerankResult)

    @respx.mock
    def test_request_body_shape(self) -> None:
        route = respx.post(COHERE_URL).mock(
            return_value=httpx.Response(200, json={"results": []}),
        )
        backend = CohereRerankBackend(api_key="my-key")
        backend.rerank("the query", ("a", "b", "c"), top_k=2)
        sent = _json.loads(route.calls.last.request.content)
        assert sent["model"] == "rerank-multilingual-v3.0"
        assert sent["query"] == "the query"
        assert sent["documents"] == ["a", "b", "c"]
        assert sent["top_n"] == 2

    @respx.mock
    def test_bearer_auth_header(self) -> None:
        route = respx.post(COHERE_URL).mock(
            return_value=httpx.Response(200, json={"results": []}),
        )
        backend = CohereRerankBackend(api_key="my-secret")
        backend.rerank("q", ("a",), top_k=1)
        sent = route.calls.last.request
        assert sent.headers["Authorization"] == "Bearer my-secret"

    @respx.mock
    def test_429_raises_rate_limit_error(self) -> None:
        respx.post(COHERE_URL).mock(return_value=httpx.Response(429))
        backend = CohereRerankBackend(api_key="test-key")
        with pytest.raises(RateLimitError):
            backend.rerank("q", ("a", "b"), top_k=2)

    @respx.mock
    def test_401_raises_auth_error_mentioning_env_var(self) -> None:
        respx.post(COHERE_URL).mock(return_value=httpx.Response(401))
        backend = CohereRerankBackend(api_key="bad-key")
        with pytest.raises(AuthError, match="COHERE_API_KEY"):
            backend.rerank("q", ("a",), top_k=1)

    @respx.mock
    def test_403_raises_auth_error(self) -> None:
        respx.post(COHERE_URL).mock(return_value=httpx.Response(403))
        backend = CohereRerankBackend(api_key="bad-key")
        with pytest.raises(AuthError):
            backend.rerank("q", ("a",), top_k=1)

    @respx.mock
    def test_500_returns_empty_tuple(self) -> None:
        respx.post(COHERE_URL).mock(return_value=httpx.Response(500))
        backend = CohereRerankBackend(api_key="test-key")
        assert backend.rerank("q", ("a", "b"), top_k=2) == ()

    @respx.mock
    def test_connection_error_returns_empty_tuple(self) -> None:
        respx.post(COHERE_URL).mock(side_effect=httpx.ConnectError("refused"))
        backend = CohereRerankBackend(api_key="test-key")
        assert backend.rerank("q", ("a",), top_k=1) == ()

    @respx.mock
    def test_invalid_json_returns_empty_tuple(self) -> None:
        respx.post(COHERE_URL).mock(return_value=httpx.Response(200, text="not-json{"))
        backend = CohereRerankBackend(api_key="test-key")
        assert backend.rerank("q", ("a",), top_k=1) == ()

    @respx.mock
    def test_unexpected_response_shape_returns_empty(self) -> None:
        respx.post(COHERE_URL).mock(return_value=httpx.Response(200, json=[]))
        backend = CohereRerankBackend(api_key="test-key")
        assert backend.rerank("q", ("a",), top_k=1) == ()

    @respx.mock
    def test_skips_results_with_invalid_index(self) -> None:
        respx.post(COHERE_URL).mock(return_value=httpx.Response(200, json={
            "results": [
                {"index": 0, "relevance_score": 0.9},
                {"index": 99, "relevance_score": 0.8},  # out of range
                {"index": "not-int", "relevance_score": 0.7},
                {"index": 1, "relevance_score": 0.6},
            ],
        }))
        backend = CohereRerankBackend(api_key="test-key")
        results = backend.rerank("q", ("a", "b"), top_k=5)
        assert len(results) == 2
        assert {r.original_index for r in results} == {0, 1}

    @respx.mock
    def test_top_k_caps_results(self) -> None:
        respx.post(COHERE_URL).mock(return_value=httpx.Response(200, json={
            "results": [
                {"index": i, "relevance_score": 1.0 - i * 0.1}
                for i in range(5)
            ],
        }))
        backend = CohereRerankBackend(api_key="test-key")
        results = backend.rerank("q", tuple(f"d{i}" for i in range(5)), top_k=2)
        assert len(results) == 2
