"""SearXNG search backend."""
from __future__ import annotations

import httpx

from llm_code.tools.search_backends import SearchResult


class SearXNGBackend:
    """Search backend using a self-hosted SearXNG instance."""

    def __init__(self, base_url: str) -> None:
        """Initialize with SearXNG instance base URL.

        Args:
            base_url: Base URL of SearXNG instance (e.g. http://localhost:8080).

        Raises:
            ValueError: If base_url is empty or whitespace.
        """
        if not base_url or not base_url.strip():
            raise ValueError("base_url must not be empty")
        self._base_url = base_url.rstrip("/")

    @property
    def name(self) -> str:
        return "searxng"

    def search(self, query: str, *, max_results: int = 10) -> tuple[SearchResult, ...]:
        """Search via SearXNG JSON API.

        Args:
            query: Search query string.
            max_results: Maximum number of results to return.

        Returns:
            Tuple of SearchResult, or empty tuple on error.
        """
        search_url = f"{self._base_url}/search"
        try:
            response = httpx.get(
                search_url,
                params={
                    "q": query,
                    "format": "json",
                    "pageno": 1,
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
