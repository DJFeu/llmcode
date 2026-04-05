"""Brave Search backend."""
from __future__ import annotations

import httpx

from llm_code.tools.search_backends import SearchResult

_BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"


class BraveBackend:
    """Search backend using Brave Search API (free tier: 2000 queries/month)."""

    def __init__(self, api_key: str) -> None:
        if not api_key or not api_key.strip():
            raise ValueError("api_key must not be empty")
        self._api_key = api_key

    @property
    def name(self) -> str:
        return "brave"

    def search(self, query: str, *, max_results: int = 10) -> tuple[SearchResult, ...]:
        try:
            response = httpx.get(
                _BRAVE_SEARCH_URL,
                params={"q": query, "count": max_results},
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "X-Subscription-Token": self._api_key,
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

        web_results = data.get("web", {}).get("results", [])
        results = tuple(
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("description", ""),
            )
            for r in web_results[:max_results]
            if r.get("url")
        )
        return results
