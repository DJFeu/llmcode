"""Tests for Exa search backend (v2.7.0a1 M1)."""
from __future__ import annotations

import httpx
import pytest
import respx

from llm_code.tools.search_backends import RateLimitError, SearchResult, create_backend
from llm_code.tools.search_backends.exa import ExaBackend

EXA_URL = "https://api.exa.ai/search"


class TestExaBackendConstruction:
    """Backend construction + identity."""

    def test_backend_name(self) -> None:
        backend = ExaBackend(api_key="test-key")
        assert backend.name == "exa"

    def test_empty_api_key_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="api_key"):
            ExaBackend(api_key="")

    def test_whitespace_api_key_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="api_key"):
            ExaBackend(api_key="   ")


class TestExaBackendSearch:
    """End-to-end search flow with mocked HTTP."""

    @respx.mock
    def test_search_success(self) -> None:
        respx.post(EXA_URL).mock(return_value=httpx.Response(200, json={
            "results": [
                {
                    "title": "Vector DBs explained",
                    "url": "https://example.com/post",
                    "text": "A long-form post about vector databases.",
                },
                {
                    "title": "Neural search 101",
                    "url": "https://another.com/article",
                    "text": "Why semantic search beats keyword on research queries.",
                },
            ],
        }))
        backend = ExaBackend(api_key="test-key")
        results = backend.search("vector databases", max_results=10)
        assert len(results) == 2
        assert isinstance(results[0], SearchResult)
        assert results[0].title == "Vector DBs explained"
        assert results[0].url == "https://example.com/post"
        assert "vector databases" in results[0].snippet

    @respx.mock
    def test_search_returns_tuple(self) -> None:
        respx.post(EXA_URL).mock(return_value=httpx.Response(200, json={
            "results": [
                {"title": "T", "url": "https://x.com", "text": "S"},
            ],
        }))
        backend = ExaBackend(api_key="test-key")
        results = backend.search("query", max_results=5)
        assert isinstance(results, tuple)

    @respx.mock
    def test_search_sends_bearer_auth_header(self) -> None:
        route = respx.post(EXA_URL).mock(
            return_value=httpx.Response(200, json={"results": []}),
        )
        backend = ExaBackend(api_key="my-secret")
        backend.search("q", max_results=3)
        sent = route.calls.last.request
        assert sent.headers["Authorization"] == "Bearer my-secret"
        assert sent.headers["Content-Type"] == "application/json"

    @respx.mock
    def test_search_sends_expected_body(self) -> None:
        import json as _json
        route = respx.post(EXA_URL).mock(
            return_value=httpx.Response(200, json={"results": []}),
        )
        backend = ExaBackend(api_key="test-key")
        backend.search("research query", max_results=7)
        sent = _json.loads(route.calls.last.request.content)
        assert sent["query"] == "research query"
        assert sent["numResults"] == 7
        assert sent["type"] == "auto"
        assert "contents" in sent

    @respx.mock
    def test_search_429_raises_rate_limit_error(self) -> None:
        respx.post(EXA_URL).mock(return_value=httpx.Response(429))
        backend = ExaBackend(api_key="test-key")
        with pytest.raises(RateLimitError):
            backend.search("test", max_results=10)

    @respx.mock
    def test_search_401_raises_auth_error_mentioning_env_var(self) -> None:
        respx.post(EXA_URL).mock(return_value=httpx.Response(401))
        backend = ExaBackend(api_key="bad-key")
        with pytest.raises(ValueError, match="EXA_API_KEY"):
            backend.search("test", max_results=10)

    @respx.mock
    def test_search_403_raises_auth_error(self) -> None:
        respx.post(EXA_URL).mock(return_value=httpx.Response(403))
        backend = ExaBackend(api_key="bad-key")
        with pytest.raises(ValueError, match="EXA_API_KEY"):
            backend.search("test", max_results=10)

    @respx.mock
    def test_search_500_returns_empty(self) -> None:
        respx.post(EXA_URL).mock(return_value=httpx.Response(500))
        backend = ExaBackend(api_key="test-key")
        results = backend.search("test", max_results=10)
        assert results == ()

    @respx.mock
    def test_search_connection_error_returns_empty(self) -> None:
        respx.post(EXA_URL).mock(side_effect=httpx.ConnectError("refused"))
        backend = ExaBackend(api_key="test-key")
        results = backend.search("test", max_results=10)
        assert results == ()

    @respx.mock
    def test_search_invalid_json_returns_empty(self) -> None:
        respx.post(EXA_URL).mock(
            return_value=httpx.Response(200, text="not-json{"),
        )
        backend = ExaBackend(api_key="test-key")
        results = backend.search("test", max_results=10)
        assert results == ()

    @respx.mock
    def test_search_skips_entries_without_url(self) -> None:
        respx.post(EXA_URL).mock(return_value=httpx.Response(200, json={
            "results": [
                {"title": "Has url", "url": "https://ok.com", "text": "ok"},
                {"title": "No url", "text": "skip"},
            ],
        }))
        backend = ExaBackend(api_key="test-key")
        results = backend.search("q", max_results=10)
        assert len(results) == 1
        assert results[0].url == "https://ok.com"

    @respx.mock
    def test_search_respects_max_results(self) -> None:
        respx.post(EXA_URL).mock(return_value=httpx.Response(200, json={
            "results": [
                {
                    "title": f"Title {i}",
                    "url": f"https://example{i}.com",
                    "text": f"Text {i}",
                }
                for i in range(10)
            ],
        }))
        backend = ExaBackend(api_key="test-key")
        results = backend.search("q", max_results=3)
        assert len(results) <= 3

    @respx.mock
    def test_snippet_truncated_to_280_chars(self) -> None:
        long_text = "x" * 1500
        respx.post(EXA_URL).mock(return_value=httpx.Response(200, json={
            "results": [
                {"title": "Long", "url": "https://x.com", "text": long_text},
            ],
        }))
        backend = ExaBackend(api_key="test-key")
        results = backend.search("q", max_results=1)
        assert len(results) == 1
        assert len(results[0].snippet) == 280


class TestExaFactory:
    """create_backend('exa') registration."""

    def test_create_backend_exa(self) -> None:
        backend = create_backend("exa", api_key="test-key")
        assert backend.name == "exa"

    def test_create_backend_exa_empty_key_raises(self) -> None:
        with pytest.raises(ValueError, match="api_key"):
            create_backend("exa", api_key="")
