"""Tests for Linkup search backend (v2.7.0a1 M3)."""
from __future__ import annotations

import json as _json

import httpx
import pytest
import respx

from llm_code.tools.search_backends import RateLimitError, SearchResult, create_backend
from llm_code.tools.search_backends.linkup import LinkupBackend

LINKUP_URL = "https://api.linkup.so/v1/search"


class TestLinkupConstruction:
    def test_backend_name(self) -> None:
        backend = LinkupBackend(api_key="test-key")
        assert backend.name == "linkup"

    def test_empty_api_key_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="api_key"):
            LinkupBackend(api_key="")

    def test_whitespace_api_key_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="api_key"):
            LinkupBackend(api_key="   ")


class TestLinkupSearch:
    @respx.mock
    def test_search_success_canonical_fields(self) -> None:
        respx.post(LINKUP_URL).mock(return_value=httpx.Response(200, json={
            "results": [
                {
                    "name": "Test Article",
                    "url": "https://example.com/article",
                    "content": "An article about Linkup search.",
                },
                {
                    "name": "Another Post",
                    "url": "https://another.com/post",
                    "content": "Another result body.",
                },
            ],
        }))
        backend = LinkupBackend(api_key="test-key")
        results = backend.search("test query", max_results=10)
        assert len(results) == 2
        assert isinstance(results[0], SearchResult)
        assert results[0].title == "Test Article"
        assert results[0].url == "https://example.com/article"
        assert results[0].snippet == "An article about Linkup search."

    @respx.mock
    def test_search_falls_back_to_legacy_field_names(self) -> None:
        respx.post(LINKUP_URL).mock(return_value=httpx.Response(200, json={
            "results": [
                {
                    "title": "Legacy Title",
                    "url": "https://x.com/legacy",
                    "snippet": "Legacy snippet body.",
                },
            ],
        }))
        backend = LinkupBackend(api_key="test-key")
        results = backend.search("q", max_results=5)
        assert len(results) == 1
        assert results[0].title == "Legacy Title"
        assert results[0].snippet == "Legacy snippet body."

    @respx.mock
    def test_search_returns_tuple(self) -> None:
        respx.post(LINKUP_URL).mock(return_value=httpx.Response(200, json={"results": []}))
        backend = LinkupBackend(api_key="test-key")
        results = backend.search("q", max_results=3)
        assert isinstance(results, tuple)

    @respx.mock
    def test_search_sends_bearer_auth_header(self) -> None:
        route = respx.post(LINKUP_URL).mock(
            return_value=httpx.Response(200, json={"results": []}),
        )
        backend = LinkupBackend(api_key="my-secret")
        backend.search("q", max_results=3)
        sent = route.calls.last.request
        assert sent.headers["Authorization"] == "Bearer my-secret"
        assert sent.headers["Content-Type"] == "application/json"

    @respx.mock
    def test_search_sends_expected_body(self) -> None:
        route = respx.post(LINKUP_URL).mock(
            return_value=httpx.Response(200, json={"results": []}),
        )
        backend = LinkupBackend(api_key="test-key")
        backend.search("query terms", max_results=7)
        sent = _json.loads(route.calls.last.request.content)
        assert sent["q"] == "query terms"
        assert sent["depth"] == "standard"
        assert sent["outputType"] == "searchResults"
        assert sent["includeImages"] is False

    @respx.mock
    def test_search_429_raises_rate_limit_error(self) -> None:
        respx.post(LINKUP_URL).mock(return_value=httpx.Response(429))
        backend = LinkupBackend(api_key="test-key")
        with pytest.raises(RateLimitError):
            backend.search("test", max_results=10)

    @respx.mock
    def test_search_401_raises_auth_error_mentioning_env_var(self) -> None:
        respx.post(LINKUP_URL).mock(return_value=httpx.Response(401))
        backend = LinkupBackend(api_key="bad-key")
        with pytest.raises(ValueError, match="LINKUP_API_KEY"):
            backend.search("test", max_results=10)

    @respx.mock
    def test_search_403_raises_auth_error(self) -> None:
        respx.post(LINKUP_URL).mock(return_value=httpx.Response(403))
        backend = LinkupBackend(api_key="bad-key")
        with pytest.raises(ValueError, match="LINKUP_API_KEY"):
            backend.search("test", max_results=10)

    @respx.mock
    def test_search_500_returns_empty(self) -> None:
        respx.post(LINKUP_URL).mock(return_value=httpx.Response(500))
        backend = LinkupBackend(api_key="test-key")
        results = backend.search("test", max_results=10)
        assert results == ()

    @respx.mock
    def test_search_connection_error_returns_empty(self) -> None:
        respx.post(LINKUP_URL).mock(side_effect=httpx.ConnectError("refused"))
        backend = LinkupBackend(api_key="test-key")
        results = backend.search("test", max_results=10)
        assert results == ()

    @respx.mock
    def test_search_invalid_json_returns_empty(self) -> None:
        respx.post(LINKUP_URL).mock(return_value=httpx.Response(200, text="not-json{"))
        backend = LinkupBackend(api_key="test-key")
        assert backend.search("test", max_results=10) == ()

    @respx.mock
    def test_search_skips_entries_without_url(self) -> None:
        respx.post(LINKUP_URL).mock(return_value=httpx.Response(200, json={
            "results": [
                {"name": "Has url", "url": "https://ok.com", "content": "ok"},
                {"name": "No url", "content": "skip"},
            ],
        }))
        backend = LinkupBackend(api_key="test-key")
        results = backend.search("q", max_results=10)
        assert len(results) == 1
        assert results[0].url == "https://ok.com"

    @respx.mock
    def test_search_respects_max_results(self) -> None:
        respx.post(LINKUP_URL).mock(return_value=httpx.Response(200, json={
            "results": [
                {
                    "name": f"Title {i}",
                    "url": f"https://example{i}.com",
                    "content": f"Body {i}",
                }
                for i in range(10)
            ],
        }))
        backend = LinkupBackend(api_key="test-key")
        results = backend.search("q", max_results=3)
        assert len(results) <= 3

    @respx.mock
    def test_snippet_truncated_to_280_chars(self) -> None:
        respx.post(LINKUP_URL).mock(return_value=httpx.Response(200, json={
            "results": [
                {"name": "Long", "url": "https://x.com", "content": "y" * 1500},
            ],
        }))
        backend = LinkupBackend(api_key="test-key")
        results = backend.search("long", max_results=1)
        assert len(results) == 1
        assert len(results[0].snippet) == 280

    @respx.mock
    def test_search_handles_unexpected_top_level_shape(self) -> None:
        # Defensive: API change → top-level not a dict-with-results → empty tuple.
        respx.post(LINKUP_URL).mock(return_value=httpx.Response(200, json=[]))
        backend = LinkupBackend(api_key="test-key")
        assert backend.search("q", max_results=5) == ()


class TestLinkupFactory:
    def test_create_backend_linkup(self) -> None:
        backend = create_backend("linkup", api_key="test-key")
        assert backend.name == "linkup"

    def test_create_backend_linkup_empty_key_raises(self) -> None:
        with pytest.raises(ValueError, match="api_key"):
            create_backend("linkup", api_key="")
