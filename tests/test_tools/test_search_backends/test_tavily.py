"""Tests for Tavily search backend."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from llm_code.tools.search_backends import SearchResult
from llm_code.tools.search_backends.tavily import TavilyBackend


class TestTavilyBackend:
    """Test TavilyBackend."""

    def test_backend_name(self) -> None:
        backend = TavilyBackend(api_key="test-key")
        assert backend.name == "tavily"

    def test_empty_api_key_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="api_key"):
            TavilyBackend(api_key="")

    def test_whitespace_api_key_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="api_key"):
            TavilyBackend(api_key="   ")

    def test_search_success(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {"title": "Title 1", "url": "https://example.com", "content": "Snippet 1"},
                {"title": "Title 2", "url": "https://another.com", "content": "Snippet 2"},
            ]
        }

        backend = TavilyBackend(api_key="test-key")
        with patch("httpx.post", return_value=mock_response):
            results = backend.search("test query", max_results=10)

        assert len(results) == 2
        assert isinstance(results[0], SearchResult)
        assert results[0].title == "Title 1"
        assert results[0].url == "https://example.com"
        assert results[0].snippet == "Snippet 1"

    def test_search_returns_tuple(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": [
            {"title": "T", "url": "https://x.com", "content": "S"},
        ]}

        backend = TavilyBackend(api_key="test-key")
        with patch("httpx.post", return_value=mock_response):
            results = backend.search("query", max_results=5)

        assert isinstance(results, tuple)

    def test_search_handles_http_error(self) -> None:
        import httpx

        backend = TavilyBackend(api_key="test-key")
        with patch("httpx.post", side_effect=httpx.RequestError("connection failed")):
            results = backend.search("test query", max_results=5)

        assert results == ()

    def test_search_handles_non_200_status(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"

        backend = TavilyBackend(api_key="bad-key")
        with patch("httpx.post", return_value=mock_response):
            results = backend.search("test query", max_results=5)

        assert results == ()

    def test_search_respects_max_results(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {"title": f"Title {i}", "url": f"https://example{i}.com", "content": f"Snippet {i}"}
                for i in range(10)
            ]
        }

        backend = TavilyBackend(api_key="test-key")
        with patch("httpx.post", return_value=mock_response):
            results = backend.search("query", max_results=3)

        assert len(results) <= 3
