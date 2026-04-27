"""Linkup search backend (linkup.so).

Free tier: 1000 searches / month.
Docs: https://docs.linkup.so/

Why Linkup?
-----------

Linkup is an AI-native search API — it treats search as a RAG step
and can return either raw results (`outputType: "searchResults"`) or
a sourced answer with citations (`outputType: "sourcedAnswer"`). For
v2.7.0a1 we wire only the raw-results mode so Linkup behaves as a
normal search backend; the sourced-answer mode is a v2.7.0 GA
candidate.

Auth header
-----------

Linkup accepts ``Authorization: Bearer <key>`` (per their REST docs).
401 / 403 surfaces as a clear `ValueError` mentioning the env var so
misconfigured deployments fail loudly instead of silently burning
free-tier quota.
"""
from __future__ import annotations

import httpx

from llm_code.tools.search_backends import RateLimitError, SearchResult

_LINKUP_SEARCH_URL = "https://api.linkup.so/v1/search"

# Match other backends' snippet length so the unified result format
# stays compact and visually balanced in the LLM's context window.
_SNIPPET_MAX_CHARS = 280


class LinkupBackend:
    """Search backend using Linkup AI-native search.

    Free tier: 1000 queries / month.
    Docs: https://docs.linkup.so/
    """

    def __init__(self, api_key: str) -> None:
        """Initialize with Linkup API key.

        Args:
            api_key: Linkup API key.

        Raises:
            ValueError: If ``api_key`` is empty or whitespace.
        """
        if not api_key or not api_key.strip():
            raise ValueError("api_key must not be empty")
        self._api_key = api_key

    @property
    def name(self) -> str:
        return "linkup"

    def search(self, query: str, *, max_results: int = 10) -> tuple[SearchResult, ...]:
        """Search via Linkup API.

        Args:
            query: Search query string.
            max_results: Maximum number of results to return.

        Returns:
            Tuple of :class:`SearchResult`, or empty tuple on transport /
            parse error. ``RateLimitError`` is raised on HTTP 429 so the
            fallback chain can move on without conflating it with an
            empty-result situation.
        """
        try:
            response = httpx.post(
                _LINKUP_SEARCH_URL,
                json={
                    "q": query,
                    "depth": "standard",
                    "outputType": "searchResults",
                    "includeImages": False,
                },
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=15.0,
            )
        except httpx.RequestError:
            return ()

        if response.status_code == 429:
            raise RateLimitError("Linkup rate limited (HTTP 429)")
        if response.status_code in (401, 403):
            raise ValueError(
                "Linkup API authentication failed — check the LINKUP_API_KEY env var "
                f"(HTTP {response.status_code})"
            )
        if response.status_code != 200:
            return ()

        try:
            data = response.json()
        except Exception:
            return ()

        # Linkup returns ``{"results": [...]}`` for outputType=searchResults.
        # Each entry has at minimum ``name`` (title), ``url``, ``content``.
        # Defensive shape handling so a future API tweak does not crash
        # the search path.
        raw_results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(raw_results, list):
            return ()

        results: list[SearchResult] = []
        for r in raw_results[:max_results]:
            if not isinstance(r, dict):
                continue
            url = r.get("url", "") or ""
            if not url:
                continue
            # Linkup uses ``name`` for title and ``content`` for the
            # snippet; older / non-canonical responses sometimes use
            # ``title``/``snippet``. Try the canonical fields first.
            title = r.get("name") or r.get("title") or ""
            snippet_src = (
                r.get("content")
                or r.get("snippet")
                or r.get("description")
                or ""
            )
            snippet = snippet_src[:_SNIPPET_MAX_CHARS] if snippet_src else ""
            results.append(SearchResult(title=title, url=url, snippet=snippet))
        return tuple(results)
