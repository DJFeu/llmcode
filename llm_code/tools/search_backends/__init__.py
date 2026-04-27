"""Search backend protocol and factory."""
from __future__ import annotations

import dataclasses
from typing import Protocol, runtime_checkable


@dataclasses.dataclass(frozen=True)
class SearchResult:
    """A single search result."""

    title: str
    url: str
    snippet: str


class RateLimitError(Exception):
    """Raised when a search backend is rate-limited."""


@runtime_checkable
class SearchBackend(Protocol):
    """Protocol for search backends."""

    @property
    def name(self) -> str:
        """Backend identifier."""
        ...

    def search(self, query: str, *, max_results: int = 10) -> tuple[SearchResult, ...]:
        """Execute search and return results.

        Returns empty tuple on error.
        """
        ...


def create_backend(backend_name: str, **kwargs: object) -> SearchBackend:
    """Factory function to create a search backend by name.

    Args:
        backend_name: One of "duckduckgo", "brave", "exa", "jina",
            "searxng", "serper", "tavily".
        **kwargs: Backend-specific keyword arguments (e.g. api_key, base_url).

    Raises:
        ValueError: If backend_name is not recognized.
    """
    if backend_name == "duckduckgo":
        from llm_code.tools.search_backends.duckduckgo import DuckDuckGoBackend
        return DuckDuckGoBackend(**kwargs)
    if backend_name == "brave":
        from llm_code.tools.search_backends.brave import BraveBackend
        return BraveBackend(**kwargs)
    if backend_name == "exa":
        # v2.7.0a1 M1 — Exa semantic / neural search (free 1000/mo).
        from llm_code.tools.search_backends.exa import ExaBackend
        return ExaBackend(**kwargs)
    if backend_name == "jina":
        # v2.7.0a1 M2 — Jina Reader search (free anonymous + key-tier).
        from llm_code.tools.search_backends.jina import JinaSearchBackend
        return JinaSearchBackend(**kwargs)
    if backend_name == "tavily":
        from llm_code.tools.search_backends.tavily import TavilyBackend
        return TavilyBackend(**kwargs)
    if backend_name == "searxng":
        from llm_code.tools.search_backends.searxng import SearXNGBackend
        return SearXNGBackend(**kwargs)
    if backend_name == "serper":
        from llm_code.tools.search_backends.serper import SerperBackend
        return SerperBackend(**kwargs)
    raise ValueError(f"Unknown search backend: {backend_name!r}")
