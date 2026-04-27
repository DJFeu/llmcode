"""Exa search backend (semantic / neural search).

Free tier: 1000 searches / month.
Docs: https://docs.exa.ai/reference/search

Why Exa?
--------

Exa is a semantic / neural search engine — it embeds queries + documents
and ranks by vector similarity rather than keyword overlap. That makes
it a strong complement to keyword backends (DuckDuckGo, Brave) for
research-style queries (papers, long-form documentation, blog posts)
where the right page does NOT necessarily contain the literal query
terms.

Auth header
-----------

The Exa REST API accepts ``Authorization: Bearer <key>`` (the canonical
form documented at https://docs.exa.ai/reference/search). The Exa
Python SDK historically also sent ``x-api-key``; we send the canonical
``Authorization`` header only — that's what the docs example uses and
it works for every Exa account tier.
"""
from __future__ import annotations

import httpx

from llm_code.tools.search_backends import RateLimitError, SearchResult

_EXA_SEARCH_URL = "https://api.exa.ai/search"

# Match other backends' snippet length so the unified result format
# stays compact and visually balanced in the LLM's context window.
_SNIPPET_MAX_CHARS = 280


class ExaBackend:
    """Search backend using Exa semantic / neural search.

    Free tier: 1000 queries / month.
    Docs: https://docs.exa.ai/reference/search
    """

    def __init__(self, api_key: str) -> None:
        """Initialize with Exa API key.

        Args:
            api_key: Exa API key.

        Raises:
            ValueError: If ``api_key`` is empty or whitespace.
        """
        if not api_key or not api_key.strip():
            raise ValueError("api_key must not be empty")
        self._api_key = api_key

    @property
    def name(self) -> str:
        return "exa"

    def search(self, query: str, *, max_results: int = 10) -> tuple[SearchResult, ...]:
        """Search via Exa API.

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
                _EXA_SEARCH_URL,
                json={
                    "query": query,
                    "numResults": max_results,
                    # "auto" lets Exa choose between neural and keyword
                    # search per query — the Exa docs recommend this as
                    # the default for general-purpose use.
                    "type": "auto",
                    "contents": {"text": {"maxCharacters": 1000}},
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
            raise RateLimitError("Exa rate limited (HTTP 429)")
        if response.status_code in (401, 403):
            # Surface auth failures clearly — silent 401s in production
            # would burn the user's free-tier quota on misconfigured
            # requests they couldn't debug.
            raise ValueError(
                "Exa API authentication failed — check the EXA_API_KEY env var "
                f"(HTTP {response.status_code})"
            )
        if response.status_code != 200:
            return ()

        try:
            data = response.json()
        except Exception:
            return ()

        raw_results = data.get("results", [])
        results: list[SearchResult] = []
        for r in raw_results[:max_results]:
            url = r.get("url", "")
            if not url:
                continue
            text = r.get("text", "") or ""
            snippet = text[:_SNIPPET_MAX_CHARS] if text else ""
            results.append(
                SearchResult(
                    title=r.get("title", "") or "",
                    url=url,
                    snippet=snippet,
                )
            )
        return tuple(results)
