"""Tests for Serper search backend."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from llm_code.tools.search_backends import SearchResult
from llm_code.tools.search_backends.serper import SerperBackend


class TestSerperBackend:
    """Test SerperBackend."""

    def test_backend_name(self) -> None:
        backend = SerperBackend(api_key="test-key")
        assert backend.name == "serper"

    def test_empty_api_key_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="api_key"):
            SerperBackend(api_key="")

    def test_whitespace_api_key_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="api_key"):
            SerperBackend(api_key="   ")

    def test_search_success(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "organic": [
                {"title": "Title 1", "link": "https://example.com", "snippet": "Snippet 1"},
                {"title": "Title 2", "link": "https://another.com", "snippet": "Snippet 2"},
            ]
        }

        backend = SerperBackend(api_key="test-key")
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
        mock_response.json.return_value = {"organic": [
            {"title": "T", "link": "https://x.com", "snippet": "S"},
        ]}

        backend = SerperBackend(api_key="test-key")
        with patch("httpx.post", return_value=mock_response):
            results = backend.search("query", max_results=5)

        assert isinstance(results, tuple)

    def test_search_sends_api_key_header(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"organic": []}

        backend = SerperBackend(api_key="my-secret")
        with patch("httpx.post", return_value=mock_response) as mock_post:
            backend.search("q", max_results=3)

        _, kwargs = mock_post.call_args
        assert kwargs["headers"]["X-API-KEY"] == "my-secret"
        assert kwargs["headers"]["Content-Type"] == "application/json"
        assert kwargs["json"] == {"q": "q", "num": 3}

    def test_search_handles_http_error(self) -> None:
        import httpx

        backend = SerperBackend(api_key="test-key")
        with patch("httpx.post", side_effect=httpx.RequestError("connection failed")):
            results = backend.search("test query", max_results=5)

        assert results == ()

    def test_search_handles_non_200_status(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = "Forbidden"

        backend = SerperBackend(api_key="bad-key")
        with patch("httpx.post", return_value=mock_response):
            results = backend.search("test query", max_results=5)

        assert results == ()

    def test_search_handles_invalid_json(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("invalid json")

        backend = SerperBackend(api_key="test-key")
        with patch("httpx.post", return_value=mock_response):
            results = backend.search("q", max_results=5)

        assert results == ()

    def test_search_skips_entries_without_link(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "organic": [
                {"title": "Has link", "link": "https://ok.com", "snippet": "ok"},
                {"title": "No link", "snippet": "skip"},
            ]
        }

        backend = SerperBackend(api_key="test-key")
        with patch("httpx.post", return_value=mock_response):
            results = backend.search("q", max_results=10)

        assert len(results) == 1
        assert results[0].url == "https://ok.com"

    def test_search_respects_max_results(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "organic": [
                {"title": f"Title {i}", "link": f"https://example{i}.com", "snippet": f"Snippet {i}"}
                for i in range(10)
            ]
        }

        backend = SerperBackend(api_key="test-key")
        with patch("httpx.post", return_value=mock_response):
            results = backend.search("query", max_results=3)

        assert len(results) <= 3


class TestSerperFactory:
    """Test create_backend('serper')."""

    def test_create_backend_serper(self) -> None:
        from llm_code.tools.search_backends import create_backend

        backend = create_backend("serper", api_key="test-key")
        assert backend.name == "serper"

    def test_create_backend_serper_empty_key_raises(self) -> None:
        from llm_code.tools.search_backends import create_backend

        with pytest.raises(ValueError, match="api_key"):
            create_backend("serper", api_key="")
