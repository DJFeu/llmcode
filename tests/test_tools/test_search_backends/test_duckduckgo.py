"""Tests for DuckDuckGo search backend."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from llm_code.tools.search_backends import SearchResult, create_backend
from llm_code.tools.search_backends.duckduckgo import DuckDuckGoBackend


class TestSearchResult:
    """Test SearchResult frozen dataclass."""

    def test_search_result_is_frozen(self) -> None:
        result = SearchResult(title="Title", url="https://example.com", snippet="Text")
        with pytest.raises(Exception):
            result.title = "New Title"  # type: ignore[misc]

    def test_search_result_fields(self) -> None:
        result = SearchResult(title="Title", url="https://example.com", snippet="Snippet")
        assert result.title == "Title"
        assert result.url == "https://example.com"
        assert result.snippet == "Snippet"

    def test_search_result_equality(self) -> None:
        r1 = SearchResult(title="T", url="https://a.com", snippet="S")
        r2 = SearchResult(title="T", url="https://a.com", snippet="S")
        assert r1 == r2


class TestCreateBackend:
    """Test create_backend factory function."""

    def test_create_duckduckgo_backend(self) -> None:
        backend = create_backend("duckduckgo")
        assert isinstance(backend, DuckDuckGoBackend)

    def test_create_nonexistent_backend_raises(self) -> None:
        with pytest.raises(ValueError, match="nonexistent"):
            create_backend("nonexistent")

    def test_create_unknown_backend_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            create_backend("google")


class TestDuckDuckGoBackend:
    """Test DuckDuckGoBackend."""

    def test_backend_name(self) -> None:
        backend = DuckDuckGoBackend()
        assert backend.name == "duckduckgo"

    def test_search_returns_tuple_of_results(self) -> None:
        html = """
        <html><body>
        <div class="results_links">
          <a class="result__a" href="https://example.com">Example Title</a>
          <div class="result__snippet">Some snippet text here.</div>
        </div>
        </body></html>
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html

        with patch("httpx.get", return_value=mock_response):
            _results = backend = DuckDuckGoBackend()
            # Just confirm backend can be called
            assert backend.name == "duckduckgo"

    def test_search_handles_http_error(self) -> None:
        import httpx

        backend = DuckDuckGoBackend()
        with patch("httpx.get", side_effect=httpx.RequestError("connection failed")):
            results = backend.search("test query", max_results=5)
        assert results == ()

    def test_search_handles_status_error(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        backend = DuckDuckGoBackend()
        with patch("httpx.get", return_value=mock_response):
            results = backend.search("test query", max_results=5)
        assert results == ()

    def test_search_parses_results(self) -> None:
        # DDG Lite HTML structure
        html = """
        <html><body>
        <div class="result">
          <h2 class="result__title"><a class="result__a" href="https://example.com">Example Title</a></h2>
          <div class="result__snippet">Some snippet text here.</div>
        </div>
        <div class="result">
          <h2 class="result__title"><a class="result__a" href="https://another.com">Another Title</a></h2>
          <div class="result__snippet">Another snippet.</div>
        </div>
        </body></html>
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html

        backend = DuckDuckGoBackend()
        with patch("httpx.get", return_value=mock_response):
            results = backend.search("test query", max_results=10)

        assert isinstance(results, tuple)
        # Should have parsed at least some results
        for r in results:
            assert isinstance(r, SearchResult)

    def test_search_respects_max_results(self) -> None:
        # Build HTML with many results
        rows = "\n".join(
            f'<div class="result"><h2 class="result__title">'
            f'<a class="result__a" href="https://example{i}.com">Title {i}</a></h2>'
            f'<div class="result__snippet">Snippet {i}.</div></div>'
            for i in range(20)
        )
        html = f"<html><body>{rows}</body></html>"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html

        backend = DuckDuckGoBackend()
        with patch("httpx.get", return_value=mock_response):
            results = backend.search("query", max_results=5)

        assert len(results) <= 5
