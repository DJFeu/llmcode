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
        backend_name: One of "duckduckgo", "brave", "tavily", "searxng".
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
    if backend_name == "tavily":
        from llm_code.tools.search_backends.tavily import TavilyBackend
        return TavilyBackend(**kwargs)
    if backend_name == "searxng":
        from llm_code.tools.search_backends.searxng import SearXNGBackend
        return SearXNGBackend(**kwargs)
    raise ValueError(f"Unknown search backend: {backend_name!r}")
