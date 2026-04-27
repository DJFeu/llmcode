"""Tests for Jina Reader search backend (v2.7.0a1 M2)."""
from __future__ import annotations

import httpx
import pytest
import respx

from llm_code.tools.search_backends import RateLimitError, SearchResult, create_backend
from llm_code.tools.search_backends.jina import JinaSearchBackend


def _jina_url_for(query: str) -> str:
    from urllib.parse import quote
    return f"https://s.jina.ai/{quote(query, safe='')}"


class TestJinaConstruction:
    def test_backend_name(self) -> None:
        backend = JinaSearchBackend()
        assert backend.name == "jina"

    def test_anonymous_no_key_allowed(self) -> None:
        # Jina supports anonymous tier — empty key must not raise.
        backend = JinaSearchBackend(api_key="")
        assert backend.name == "jina"

    def test_whitespace_key_normalised_to_empty(self) -> None:
        # Whitespace-only env var → anonymous mode, not auth header.
        backend = JinaSearchBackend(api_key="   ")
        # Implementation detail: stored key is normalised so the
        # Authorization header is omitted.
        assert backend._api_key == ""


class TestJinaSearch:
    @respx.mock
    def test_search_success_with_data_envelope(self) -> None:
        url = _jina_url_for("vector databases")
        respx.get(url).mock(return_value=httpx.Response(200, json={
            "code": 200,
            "status": 20000,
            "data": [
                {
                    "title": "Vector DBs",
                    "url": "https://example.com/post",
                    "description": "An overview of vector databases.",
                },
                {
                    "title": "Pinecone vs Weaviate",
                    "url": "https://another.com/post2",
                    "content": "Comparison post body.",
                },
            ],
        }))
        backend = JinaSearchBackend()
        results = backend.search("vector databases", max_results=10)
        assert len(results) == 2
        assert isinstance(results[0], SearchResult)
        assert results[0].title == "Vector DBs"
        assert results[0].url == "https://example.com/post"
        assert "vector databases" in results[0].snippet
        # Falls back from `description` → `content` for second entry.
        assert results[1].snippet == "Comparison post body."

    @respx.mock
    def test_search_returns_tuple(self) -> None:
        url = _jina_url_for("q")
        respx.get(url).mock(return_value=httpx.Response(200, json={"data": []}))
        backend = JinaSearchBackend()
        results = backend.search("q", max_results=3)
        assert isinstance(results, tuple)

    @respx.mock
    def test_search_no_auth_header_when_anonymous(self) -> None:
        url = _jina_url_for("anon query")
        route = respx.get(url).mock(return_value=httpx.Response(200, json={"data": []}))
        backend = JinaSearchBackend()  # no key
        backend.search("anon query", max_results=5)
        sent = route.calls.last.request
        assert "Authorization" not in sent.headers

    @respx.mock
    def test_search_sends_bearer_when_key_set(self) -> None:
        url = _jina_url_for("authed")
        route = respx.get(url).mock(return_value=httpx.Response(200, json={"data": []}))
        backend = JinaSearchBackend(api_key="jina-secret")
        backend.search("authed", max_results=5)
        sent = route.calls.last.request
        assert sent.headers["Authorization"] == "Bearer jina-secret"

    @respx.mock
    def test_search_429_raises_rate_limit_error(self) -> None:
        url = _jina_url_for("rl")
        respx.get(url).mock(return_value=httpx.Response(429))
        backend = JinaSearchBackend()
        with pytest.raises(RateLimitError):
            backend.search("rl", max_results=10)

    @respx.mock
    def test_search_500_returns_empty(self) -> None:
        url = _jina_url_for("err")
        respx.get(url).mock(return_value=httpx.Response(500))
        backend = JinaSearchBackend()
        assert backend.search("err", max_results=10) == ()

    @respx.mock
    def test_search_connection_error_returns_empty(self) -> None:
        url = _jina_url_for("conn")
        respx.get(url).mock(side_effect=httpx.ConnectError("refused"))
        backend = JinaSearchBackend()
        assert backend.search("conn", max_results=10) == ()

    @respx.mock
    def test_search_invalid_json_returns_empty(self) -> None:
        url = _jina_url_for("bad")
        respx.get(url).mock(return_value=httpx.Response(200, text="not-json{"))
        backend = JinaSearchBackend()
        assert backend.search("bad", max_results=10) == ()

    @respx.mock
    def test_search_skips_entries_without_url(self) -> None:
        url = _jina_url_for("q")
        respx.get(url).mock(return_value=httpx.Response(200, json={
            "data": [
                {"title": "Has url", "url": "https://ok.com", "description": "ok"},
                {"title": "No url", "description": "skip"},
            ],
        }))
        backend = JinaSearchBackend()
        results = backend.search("q", max_results=10)
        assert len(results) == 1
        assert results[0].url == "https://ok.com"

    @respx.mock
    def test_search_respects_max_results(self) -> None:
        url = _jina_url_for("q")
        respx.get(url).mock(return_value=httpx.Response(200, json={
            "data": [
                {"title": f"T{i}", "url": f"https://ex{i}.com", "description": f"S{i}"}
                for i in range(10)
            ],
        }))
        backend = JinaSearchBackend()
        results = backend.search("q", max_results=3)
        assert len(results) <= 3

    @respx.mock
    def test_snippet_truncated_to_280_chars(self) -> None:
        url = _jina_url_for("long")
        respx.get(url).mock(return_value=httpx.Response(200, json={
            "data": [
                {"title": "Long", "url": "https://x.com", "description": "y" * 1500},
            ],
        }))
        backend = JinaSearchBackend()
        results = backend.search("long", max_results=1)
        assert len(results) == 1
        assert len(results[0].snippet) == 280

    @respx.mock
    def test_search_handles_bare_list_response(self) -> None:
        # Defensive: some Jina responses return the list directly.
        url = _jina_url_for("bare")
        respx.get(url).mock(return_value=httpx.Response(200, json=[
            {"title": "Direct", "url": "https://direct.com", "content": "d"},
        ]))
        backend = JinaSearchBackend()
        results = backend.search("bare", max_results=5)
        assert len(results) == 1
        assert results[0].url == "https://direct.com"


class TestJinaFactory:
    def test_create_backend_jina(self) -> None:
        backend = create_backend("jina")
        assert backend.name == "jina"

    def test_create_backend_jina_with_key(self) -> None:
        backend = create_backend("jina", api_key="abc")
        assert backend.name == "jina"
