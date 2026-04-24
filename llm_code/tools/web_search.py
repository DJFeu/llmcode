"""WebSearchTool — web search using configurable backends."""
from __future__ import annotations

import fnmatch
import logging
import os
import re
from datetime import date
from urllib.parse import urlparse

from pydantic import BaseModel

from llm_code.tools.base import PermissionLevel, Tool, ToolResult
from llm_code.tools.search_backends import RateLimitError, SearchResult, create_backend

logger = logging.getLogger(__name__)

_VALID_BACKENDS = ("auto", "duckduckgo", "brave", "tavily", "searxng", "serper")

_TIME_SENSITIVE_TRIGGERS: tuple[str, ...] = (
    "今日", "今天", "現在", "即時",
    "today", "latest", "current", "breaking", "right now",
)
_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def _augment_time_sensitive_query(query: str) -> str:
    """Append today's ISO date when a query signals current-moment intent
    but omits an explicit date. Prevents search engines matching stale
    month-archive pages for asks like "today's top news"."""
    lower = query.lower()
    if not any(t in lower for t in _TIME_SENSITIVE_TRIGGERS):
        return query
    if _ISO_DATE_RE.search(query):
        return query
    return f"{query} {date.today().isoformat()}"


class WebSearchInput(BaseModel):
    query: str
    max_results: int = 10
    backend: str = "auto"


class WebSearchTool(Tool):
    """Tool for performing web searches via configurable backends."""

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "Search the web for information. "
            "Supports DuckDuckGo (default), Brave, Tavily, SearXNG, and Serper backends. "
            "Returns ranked results with titles, URLs, and snippets. "
            "For 'today's news' / 'latest X' asks, include the full date "
            "(YYYY-MM-DD — see the Environment section) in the query; a "
            "month-only query matches stale month-archive pages."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default: 10).",
                    "default": 10,
                },
                "backend": {
                    "type": "string",
                    "enum": list(_VALID_BACKENDS),
                    "description": (
                        "Search backend to use. 'auto' selects based on config "
                        "(default: duckduckgo)."
                    ),
                    "default": "auto",
                },
            },
            "required": ["query"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    @property
    def input_model(self) -> type[WebSearchInput]:
        return WebSearchInput

    def is_read_only(self, args: dict) -> bool:
        # Network read — does not mutate local filesystem.
        return True

    def is_concurrency_safe(self, args: dict) -> bool:
        return True

    def _get_web_search_config(self) -> object | None:
        """Attempt to load WebSearchConfig from runtime config."""
        try:
            from llm_code.runtime.config import WebSearchConfig
            return WebSearchConfig()
        except ImportError:
            return None

    def _resolve_backend(self, backend_arg: str) -> tuple[object, str]:
        """Resolve backend name and instantiate it.

        Returns (backend_instance, backend_name).
        """
        cfg = self._get_web_search_config()

        if backend_arg == "auto":
            backend_name = "duckduckgo"
            if cfg is not None:
                backend_name = getattr(cfg, "default_backend", "duckduckgo")
        else:
            backend_name = backend_arg

        # Build kwargs for backends that need configuration
        kwargs: dict = {}
        if backend_name == "brave" and cfg is not None:
            api_key_env = getattr(cfg, "brave_api_key_env", "BRAVE_API_KEY")
            api_key = os.environ.get(api_key_env, "")
            kwargs["api_key"] = api_key
        elif backend_name == "tavily" and cfg is not None:
            api_key_env = getattr(cfg, "tavily_api_key_env", "TAVILY_API_KEY")
            api_key = os.environ.get(api_key_env, "")
            kwargs["api_key"] = api_key
        elif backend_name == "searxng" and cfg is not None:
            kwargs["base_url"] = getattr(cfg, "searxng_base_url", "")
        elif backend_name == "serper" and cfg is not None:
            api_key_env = getattr(cfg, "serper_api_key_env", "SERPER_API_KEY")
            api_key = os.environ.get(api_key_env, "")
            kwargs["api_key"] = api_key

        backend = create_backend(backend_name, **kwargs)
        return backend, backend_name

    def _filter_results(
        self,
        results: tuple[SearchResult, ...],
        *,
        domain_allowlist: tuple[str, ...],
        domain_denylist: tuple[str, ...],
    ) -> tuple[SearchResult, ...]:
        """Apply domain denylist then allowlist filtering.

        Denylist is applied first. If allowlist is non-empty, only results
        matching an allowlist pattern are kept. Empty lists pass everything.

        Args:
            results: Results to filter.
            domain_allowlist: Glob patterns for allowed domains.
            domain_denylist: Glob patterns for denied domains.

        Returns:
            Filtered tuple of SearchResult.
        """
        def _get_domain(url: str) -> str:
            try:
                return urlparse(url).netloc
            except Exception:
                return url

        filtered: list[SearchResult] = []
        for result in results:
            domain = _get_domain(result.url)

            # Apply denylist first
            if domain_denylist and any(
                fnmatch.fnmatch(domain, pattern) for pattern in domain_denylist
            ):
                continue

            # Apply allowlist
            if domain_allowlist and not any(
                fnmatch.fnmatch(domain, pattern) for pattern in domain_allowlist
            ):
                continue

            filtered.append(result)

        return tuple(filtered)

    def _format_results(self, query: str, results: tuple[SearchResult, ...]) -> str:
        """Format search results as markdown.

        Args:
            query: The original search query.
            results: Search results to format.

        Returns:
            Formatted markdown string.
        """
        lines: list[str] = [f'## Search Results for "{query}"', ""]

        if not results:
            lines.append("(0 results)")
            lines.append("")
            lines.append(
                "No results found for this query. Do NOT retry with the "
                "same search terms — try rephrasing with different keywords, "
                "a different language (e.g. English instead of Chinese), "
                "or answer based on your existing knowledge instead."
            )
            return "\n".join(lines)

        for i, result in enumerate(results, start=1):
            lines.append(f"{i}. **[{result.title}]({result.url})**")
            lines.append(f"   {result.snippet}")
            lines.append("")

        lines.append(f"({len(results)} results)")
        return "\n".join(lines)

    def execute(self, args: dict) -> ToolResult:
        """Execute a web search.

        Args:
            args: Dictionary with keys: query (required), max_results (int),
                  backend (str enum).

        Returns:
            ToolResult with formatted search results, or error.
        """
        raw_query = args.get("query", "")
        if not raw_query or not str(raw_query).strip():
            return ToolResult(
                output="Error: 'query' is required and must not be empty.",
                is_error=True,
            )

        query = _augment_time_sensitive_query(str(raw_query))
        if query != raw_query:
            logger.info("web_search: augmented query %r → %r", raw_query, query)
        max_results = int(args.get("max_results", 10))
        backend_arg = str(args.get("backend", "auto"))

        # Apply domain filtering from config
        cfg = self._get_web_search_config()
        allowlist: tuple[str, ...] = ()
        denylist: tuple[str, ...] = ()
        if cfg is not None:
            allowlist = getattr(cfg, "domain_allowlist", ())
            denylist = getattr(cfg, "domain_denylist", ())

        if backend_arg == "auto":
            # Fallback chain: try each configured backend until one returns results
            results = self._search_with_fallback(query, max_results, cfg)
        else:
            try:
                backend, _name = self._resolve_backend(backend_arg)
            except (ValueError, Exception) as exc:
                return ToolResult(
                    output=f"Error: Failed to initialize search backend: {exc}",
                    is_error=True,
                )
            try:
                results = backend.search(query, max_results=max_results)
            except Exception as exc:
                return ToolResult(
                    output=f"Error: Search failed: {exc}",
                    is_error=True,
                )

        results = self._filter_results(results, domain_allowlist=allowlist, domain_denylist=denylist)
        output = self._format_results(query, results)
        return ToolResult(output=output, is_error=False)

    def _search_with_fallback(
        self, query: str, max_results: int, cfg: object | None,
    ) -> tuple[SearchResult, ...]:
        """Try backends in order until one returns results.

        Fallback order: duckduckgo -> brave -> searxng -> serper -> tavily.
        Only backends that are configured (have API keys / base_url set) are tried.
        """
        # Build ordered list of (backend_name, kwargs) to try
        chain: list[tuple[str, dict]] = []

        # 1. DuckDuckGo (always available, no config needed)
        chain.append(("duckduckgo", {}))

        # 2. Brave (if API key configured)
        if cfg is not None:
            brave_key_env = getattr(cfg, "brave_api_key_env", "BRAVE_API_KEY")
            brave_key = os.environ.get(brave_key_env, "")
            if brave_key:
                chain.append(("brave", {"api_key": brave_key}))

        # 3. SearXNG (if base_url configured)
        if cfg is not None:
            searxng_url = getattr(cfg, "searxng_base_url", "")
            if searxng_url:
                chain.append(("searxng", {"base_url": searxng_url}))

        # 4. Serper (if API key configured)
        if cfg is not None:
            serper_key_env = getattr(cfg, "serper_api_key_env", "SERPER_API_KEY")
            serper_key = os.environ.get(serper_key_env, "")
            if serper_key:
                chain.append(("serper", {"api_key": serper_key}))

        # 5. Tavily (if API key configured)
        if cfg is not None:
            tavily_key_env = getattr(cfg, "tavily_api_key_env", "TAVILY_API_KEY")
            tavily_key = os.environ.get(tavily_key_env, "")
            if tavily_key:
                chain.append(("tavily", {"api_key": tavily_key}))

        for backend_name, kwargs in chain:
            try:
                backend = create_backend(backend_name, **kwargs)
                results = backend.search(query, max_results=max_results)
                if results:
                    return results
            except RateLimitError:
                logger.warning("Search backend %s rate-limited, trying next", backend_name)
                continue
            except Exception:
                continue

        return ()
