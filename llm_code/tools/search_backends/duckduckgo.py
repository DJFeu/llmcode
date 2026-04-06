"""DuckDuckGo Lite search backend."""
from __future__ import annotations

import time
from html.parser import HTMLParser

import httpx

from llm_code.tools.search_backends import SearchResult

_DDG_LITE_URL = "https://html.duckduckgo.com/html/"
_RATE_LIMIT_SECONDS = 1.0


class _DDGLiteParser(HTMLParser):
    """Parse DuckDuckGo Lite HTML to extract search results."""

    def __init__(self) -> None:
        super().__init__()
        self._results: list[dict[str, str]] = []
        self._in_title_link = False
        self._in_snippet = False
        self._current_url = ""
        self._current_title = ""
        self._snippet_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        classes = attr_dict.get("class", "") or ""

        if tag == "a" and "result__a" in classes:
            self._in_title_link = True
            self._current_url = attr_dict.get("href", "")
            self._current_title = ""

        if tag in ("div", "a") and "result__snippet" in classes:
            self._in_snippet = True
            self._snippet_tag = tag
            self._current_snippet = ""

    def handle_endtag(self, tag: str) -> None:
        if self._in_title_link and tag == "a":
            self._in_title_link = False

        if self._in_snippet and tag == getattr(self, "_snippet_tag", "div"):
            self._in_snippet = False
            # Save result when snippet ends
            if self._current_url and self._current_title:
                self._results.append({
                    "title": self._current_title.strip(),
                    "url": self._current_url,
                    "snippet": getattr(self, "_current_snippet", "").strip(),
                })

    def handle_data(self, data: str) -> None:
        if self._in_title_link:
            self._current_title += data
        if self._in_snippet:
            self._current_snippet += data

    def get_results(self) -> list[dict[str, str]]:
        return self._results


class DuckDuckGoBackend:
    """Search backend using DuckDuckGo Lite."""

    def __init__(self) -> None:
        self._last_request_time: float = 0.0

    @property
    def name(self) -> str:
        return "duckduckgo"

    def _rate_limit(self) -> None:
        """Enforce 1-second rate limit between requests."""
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < _RATE_LIMIT_SECONDS:
            time.sleep(_RATE_LIMIT_SECONDS - elapsed)
        self._last_request_time = time.monotonic()

    def search(self, query: str, *, max_results: int = 10) -> tuple[SearchResult, ...]:
        """Search DuckDuckGo Lite and return results.

        Args:
            query: Search query string.
            max_results: Maximum number of results to return.

        Returns:
            Tuple of SearchResult, or empty tuple on error.
        """
        self._rate_limit()

        try:
            response = httpx.get(
                _DDG_LITE_URL,
                params={"q": query},
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (compatible; llm-code/1.0; "
                        "+https://github.com/llm-code)"
                    )
                },
                timeout=10.0,
                follow_redirects=True,
            )
        except httpx.RequestError:
            return ()

        if response.status_code not in (200, 202):
            return ()

        parser = _DDGLiteParser()
        parser.feed(response.text)
        raw = parser.get_results()

        results = tuple(
            SearchResult(
                title=r["title"],
                url=self._extract_real_url(r["url"]),
                snippet=r["snippet"],
            )
            for r in raw[:max_results]
            if r.get("title") and r.get("url")
        )
        return results

    @staticmethod
    def _extract_real_url(ddg_url: str) -> str:
        """Extract the real URL from a DuckDuckGo redirect URL."""
        from urllib.parse import unquote, urlparse, parse_qs
        if "duckduckgo.com/l/" in ddg_url:
            parsed = urlparse(ddg_url)
            uddg = parse_qs(parsed.query).get("uddg", [""])[0]
            if uddg:
                return unquote(uddg)
        return ddg_url
