"""Tavily search backend."""
from __future__ import annotations

import httpx

from llm_code.tools.search_backends import SearchResult

_TAVILY_SEARCH_URL = "https://api.tavily.com/search"


class TavilyBackend:
    """Search backend using Tavily API."""

    def __init__(self, api_key: str) -> None:
        """Initialize with Tavily API key.

        Args:
            api_key: Tavily API key.

        Raises:
            ValueError: If api_key is empty or whitespace.
        """
        if not api_key or not api_key.strip():
            raise ValueError("api_key must not be empty")
        self._api_key = api_key

    @property
    def name(self) -> str:
        return "tavily"

    def search(self, query: str, *, max_results: int = 10) -> tuple[SearchResult, ...]:
        """Search via Tavily API.

        Args:
            query: Search query string.
            max_results: Maximum number of results to return.

        Returns:
            Tuple of SearchResult, or empty tuple on error.
        """
        try:
            response = httpx.post(
                _TAVILY_SEARCH_URL,
                json={
                    "api_key": self._api_key,
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "basic",
                },
                timeout=15.0,
            )
        except httpx.RequestError:
            return ()

        if response.status_code != 200:
            return ()

        try:
            data = response.json()
        except Exception:
            return ()

        raw_results = data.get("results", [])
        results = tuple(
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("content", ""),
            )
            for r in raw_results[:max_results]
            if r.get("url")
        )
        return results
