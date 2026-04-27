"""Jina Reader search backend (s.jina.ai).

Free tier: completely free, no API key required. With a key the rate
limits are looser (Jina docs note 200 RPM with key, ~20 RPM anon).
The same provider also exposes ``r.jina.ai/<url>`` which extracts a
URL into clean markdown — that's wired into ``web_fetch`` separately
(see ``llm_code/tools/web_fetch.py::fetch_via_jina_reader``).

Why Jina?
---------

Two reasons it earns a slot in the auto-fallback chain even though
DuckDuckGo / Brave / Exa already cover three flavours of search:

* The fetch path. Jina's reader is a hosted browser-render-and-extract
  service — a free alternative to running headless Chromium locally
  and a much-improved alternative to ``readability-lxml`` on JS-heavy
  pages. Wiring it as a search backend AND as the default extraction
  backend (when ``extraction_backend == "auto"``) is the highest-value
  v2.7.0a1 mechanism.
* The search endpoint is fully anonymous-friendly — useful when an
  installation runs without ANY paid keys (common in eval setups and
  open-source contributors' first sessions).

Auth header
-----------

When ``JINA_API_KEY`` is set we send ``Authorization: Bearer <key>``
(per https://jina.ai/reader/). Without a key we send no auth header —
anonymous requests work, just at a tighter rate limit.
"""
from __future__ import annotations

from urllib.parse import quote

import httpx

from llm_code.tools.search_backends import RateLimitError, SearchResult

# Match other backends' snippet length so the unified result format
# stays compact and visually balanced in the LLM's context window.
_SNIPPET_MAX_CHARS = 280


class JinaSearchBackend:
    """Search backend using Jina (s.jina.ai).

    Free tier: anonymous (no key required). With ``JINA_API_KEY`` the
    rate limit climbs from ~20 RPM to ~200 RPM.

    Docs: https://jina.ai/reader/
    """

    def __init__(self, api_key: str = "") -> None:
        """Initialize.

        Args:
            api_key: Optional Jina API key. Anonymous use is allowed —
                pass ``""`` (the default) to use the anonymous tier.
                Whitespace-only strings are normalised to ``""`` so
                callers can safely forward an unset env var.
        """
        # Empty / whitespace api_key is OK for Jina — the anonymous
        # tier is supported. We just normalise to the empty string.
        self._api_key = api_key.strip() if api_key else ""

    @property
    def name(self) -> str:
        return "jina"

    def search(self, query: str, *, max_results: int = 10) -> tuple[SearchResult, ...]:
        """Search via Jina (s.jina.ai).

        Args:
            query: Search query string.
            max_results: Maximum number of results to return.

        Returns:
            Tuple of :class:`SearchResult`, or empty tuple on error.
            HTTP 429 raises :class:`RateLimitError` so the fallback
            chain can move on without conflating rate-limit with
            empty-result.
        """
        # Jina uses path-based queries: s.jina.ai/<url-encoded-query>
        url = f"https://s.jina.ai/{quote(query, safe='')}"

        headers = {
            "Accept": "application/json",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            response = httpx.get(
                url,
                headers=headers,
                timeout=15.0,
                follow_redirects=True,
            )
        except httpx.RequestError:
            return ()

        if response.status_code == 429:
            raise RateLimitError("Jina rate limited (HTTP 429)")
        if response.status_code != 200:
            return ()

        try:
            data = response.json()
        except Exception:
            return ()

        # Jina's JSON envelope: {"code": 200, "status": ..., "data": [...]}
        # Each entry has {title, url, content/description, ...}.
        # Defensive extraction so a different provider response shape
        # does not crash the search path.
        raw_results = data.get("data") if isinstance(data, dict) else None
        if not isinstance(raw_results, list):
            # Some Jina responses return the list directly.
            raw_results = data if isinstance(data, list) else []

        results: list[SearchResult] = []
        for r in raw_results[:max_results]:
            if not isinstance(r, dict):
                continue
            url_val = r.get("url", "") or ""
            if not url_val:
                continue
            # Snippet preference: explicit description > content excerpt.
            snippet_src = (
                r.get("description")
                or r.get("content")
                or r.get("snippet")
                or ""
            )
            snippet = snippet_src[:_SNIPPET_MAX_CHARS] if snippet_src else ""
            results.append(
                SearchResult(
                    title=r.get("title", "") or "",
                    url=url_val,
                    snippet=snippet,
                )
            )
        return tuple(results)
