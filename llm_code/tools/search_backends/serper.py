"""Serper search backend (serper.dev — Google Search API)."""
from __future__ import annotations

import httpx

from llm_code.tools.search_backends import SearchResult

_SERPER_SEARCH_URL = "https://google.serper.dev/search"


class SerperBackend:
    """Search backend using Serper.dev (Google Search API).

    Free tier: 2500 queries, paid plans from 50,000 queries/month.
    Docs: https://serper.dev/
    """

    def __init__(self, api_key: str) -> None:
        """Initialize with Serper API key.

        Args:
            api_key: Serper API key.

        Raises:
            ValueError: If api_key is empty or whitespace.
        """
        if not api_key or not api_key.strip():
            raise ValueError("api_key must not be empty")
        self._api_key = api_key

    @property
    def name(self) -> str:
        return "serper"

    def search(self, query: str, *, max_results: int = 10) -> tuple[SearchResult, ...]:
        """Search via Serper API.

        Args:
            query: Search query string.
            max_results: Maximum number of results to return.

        Returns:
            Tuple of SearchResult, or empty tuple on error.
        """
        try:
            response = httpx.post(
                _SERPER_SEARCH_URL,
                json={"q": query, "num": max_results},
                headers={
                    "X-API-KEY": self._api_key,
                    "Content-Type": "application/json",
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

        organic = data.get("organic", [])
        results = tuple(
            SearchResult(
                title=r.get("title", ""),
                url=r.get("link", ""),
                snippet=r.get("snippet", ""),
            )
            for r in organic[:max_results]
            if r.get("link")
        )
        return results
