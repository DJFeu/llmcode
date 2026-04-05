"""Tests for web search fallback chain."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

from llm_code.tools.web_search import WebSearchTool
from llm_code.tools.search_backends import SearchResult


class TestFallbackChain:
    def test_fallback_to_second_backend_when_first_returns_empty(self) -> None:
        tool = WebSearchTool()
        # Mock _search_with_fallback to verify it's called for auto
        with patch.object(tool, '_search_with_fallback') as mock_fallback:
            mock_fallback.return_value = (
                SearchResult(title="Found", url="https://example.com", snippet="via fallback"),
            )
            result = tool.execute({"query": "test", "backend": "auto"})
            assert "Found" in result.output
            mock_fallback.assert_called_once()

    def test_specific_backend_no_fallback(self) -> None:
        tool = WebSearchTool()
        with patch.object(tool, '_resolve_backend') as mock_resolve:
            mock_backend = MagicMock()
            mock_backend.search.return_value = ()
            mock_resolve.return_value = (mock_backend, "duckduckgo")
            result = tool.execute({"query": "test", "backend": "duckduckgo"})
            assert "(0 results)" in result.output

    def test_auto_tries_multiple_backends(self) -> None:
        tool = WebSearchTool()
        call_count = {"n": 0}

        def mock_create(name, **kwargs):
            call_count["n"] += 1
            backend = MagicMock()
            if call_count["n"] == 1:
                backend.search.return_value = ()  # first fails
            else:
                backend.search.return_value = (
                    SearchResult(title="OK", url="https://ok.com", snippet="found"),
                )
            return backend

        with patch("llm_code.tools.web_search.create_backend", side_effect=mock_create):
            with patch.dict("os.environ", {"BRAVE_API_KEY": "test"}):
                result = tool._search_with_fallback("test", 10, MagicMock(
                    brave_api_key_env="BRAVE_API_KEY",
                    searxng_base_url="",
                    tavily_api_key_env="TAVILY_API_KEY",
                ))
                assert len(result) == 1
                assert call_count["n"] == 2  # tried DDG, then Brave
