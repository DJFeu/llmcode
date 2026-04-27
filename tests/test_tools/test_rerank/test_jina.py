"""Jina rerank backend tests (v2.8.0 M1)."""
from __future__ import annotations

import json as _json

import httpx
import pytest
import respx

from llm_code.tools.rerank import RateLimitError, RerankResult
from llm_code.tools.rerank.jina import JinaRerankBackend

JINA_URL = "https://api.jina.ai/v1/rerank"


class TestJinaConstruction:
    def test_backend_name(self) -> None:
        backend = JinaRerankBackend(api_key="test-key")
        assert backend.name == "jina"

    def test_anonymous_construction_ok(self) -> None:
        backend = JinaRerankBackend(api_key="")
        assert backend.name == "jina"

    def test_whitespace_key_normalised_to_empty(self) -> None:
        backend = JinaRerankBackend(api_key="   ")
        assert backend._api_key == ""  # type: ignore[attr-defined]


class TestJinaRerank:
    def test_empty_documents_returns_empty(self) -> None:
        backend = JinaRerankBackend(api_key="")
        assert backend.rerank("q", (), top_k=5) == ()

    @respx.mock
    def test_anonymous_no_auth_header(self) -> None:
        route = respx.post(JINA_URL).mock(
            return_value=httpx.Response(200, json={"results": []}),
        )
        backend = JinaRerankBackend(api_key="")
        backend.rerank("q", ("a",), top_k=1)
        sent = route.calls.last.request
        assert "Authorization" not in sent.headers

    @respx.mock
    def test_with_key_sends_bearer_auth(self) -> None:
        route = respx.post(JINA_URL).mock(
            return_value=httpx.Response(200, json={"results": []}),
        )
        backend = JinaRerankBackend(api_key="my-secret")
        backend.rerank("q", ("a",), top_k=1)
        sent = route.calls.last.request
        assert sent.headers["Authorization"] == "Bearer my-secret"

    @respx.mock
    def test_request_body_shape(self) -> None:
        route = respx.post(JINA_URL).mock(
            return_value=httpx.Response(200, json={"results": []}),
        )
        backend = JinaRerankBackend(api_key="")
        backend.rerank("the query", ("a", "b"), top_k=3)
        sent = _json.loads(route.calls.last.request.content)
        assert sent["model"] == "jina-reranker-v2-base-multilingual"
        assert sent["query"] == "the query"
        assert sent["documents"] == ["a", "b"]
        assert sent["top_n"] == 2  # capped to len(documents)

    @respx.mock
    def test_response_parsing_orders_by_score(self) -> None:
        respx.post(JINA_URL).mock(return_value=httpx.Response(200, json={
            "results": [
                {"index": 1, "relevance_score": 0.95},
                {"index": 0, "relevance_score": 0.30},
            ],
        }))
        backend = JinaRerankBackend(api_key="")
        results = backend.rerank("q", ("first", "second"), top_k=2)
        assert len(results) == 2
        assert results[0].document == "second"
        assert results[0].score == pytest.approx(0.95)
        assert results[1].document == "first"
        for r in results:
            assert isinstance(r, RerankResult)

    @respx.mock
    def test_429_raises_rate_limit(self) -> None:
        respx.post(JINA_URL).mock(return_value=httpx.Response(429))
        backend = JinaRerankBackend(api_key="")
        with pytest.raises(RateLimitError):
            backend.rerank("q", ("a",), top_k=1)

    @respx.mock
    def test_500_returns_empty(self) -> None:
        respx.post(JINA_URL).mock(return_value=httpx.Response(500))
        backend = JinaRerankBackend(api_key="")
        assert backend.rerank("q", ("a",), top_k=1) == ()

    @respx.mock
    def test_connection_error_returns_empty(self) -> None:
        respx.post(JINA_URL).mock(side_effect=httpx.ConnectError("refused"))
        backend = JinaRerankBackend(api_key="")
        assert backend.rerank("q", ("a",), top_k=1) == ()

    @respx.mock
    def test_invalid_json_returns_empty(self) -> None:
        respx.post(JINA_URL).mock(return_value=httpx.Response(200, text="not-json{"))
        backend = JinaRerankBackend(api_key="")
        assert backend.rerank("q", ("a",), top_k=1) == ()

    @respx.mock
    def test_unexpected_shape_returns_empty(self) -> None:
        respx.post(JINA_URL).mock(return_value=httpx.Response(200, json=[]))
        backend = JinaRerankBackend(api_key="")
        assert backend.rerank("q", ("a",), top_k=1) == ()
