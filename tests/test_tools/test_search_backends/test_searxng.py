"""Tests for SearXNG search backend."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from llm_code.tools.search_backends import SearchResult
from llm_code.tools.search_backends.searxng import SearXNGBackend


class TestSearXNGBackend:
    """Test SearXNGBackend."""

    def test_backend_name(self) -> None:
        backend = SearXNGBackend(base_url="http://localhost:8080")
        assert backend.name == "searxng"

    def test_empty_base_url_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="base_url"):
            SearXNGBackend(base_url="")

    def test_whitespace_base_url_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="base_url"):
            SearXNGBackend(base_url="   ")

    def test_search_success(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {"title": "Title 1", "url": "https://example.com", "content": "Snippet 1"},
                {"title": "Title 2", "url": "https://another.com", "content": "Snippet 2"},
            ]
        }

        backend = SearXNGBackend(base_url="http://localhost:8080")
        with patch("httpx.get", return_value=mock_response):
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

        backend = SearXNGBackend(base_url="http://localhost:8080")
        with patch("httpx.get", return_value=mock_response):
            results = backend.search("query", max_results=5)

        assert isinstance(results, tuple)

    def test_search_handles_http_error(self) -> None:
        import httpx

        backend = SearXNGBackend(base_url="http://localhost:8080")
        with patch("httpx.get", side_effect=httpx.RequestError("connection failed")):
            results = backend.search("test query", max_results=5)

        assert results == ()

    def test_search_handles_non_200_status(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        backend = SearXNGBackend(base_url="http://localhost:8080")
        with patch("httpx.get", return_value=mock_response):
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

        backend = SearXNGBackend(base_url="http://localhost:8080")
        with patch("httpx.get", return_value=mock_response):
            results = backend.search("query", max_results=3)

        assert len(results) <= 3

    def test_search_uses_correct_endpoint(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": []}

        backend = SearXNGBackend(base_url="http://localhost:8080")
        with patch("httpx.get", return_value=mock_response) as mock_get:
            backend.search("query", max_results=5)

        call_url = mock_get.call_args[0][0]
        assert "localhost:8080" in call_url
        assert "/search" in call_url
