"""WebFetch tool — fetch and extract content from URLs."""
from __future__ import annotations

import logging
import os
from typing import Literal
from urllib.parse import quote

import httpx
from pydantic import BaseModel, field_validator

from llm_code.tools.base import PermissionLevel, Tool, ToolResult
from llm_code.tools.web_common import UrlCache, classify_url, extract_content

logger = logging.getLogger(__name__)


class WebFetchInput(BaseModel):
    """Input model for WebFetchTool."""

    url: str
    prompt: str = ""
    max_length: int = 50_000
    raw: bool = False
    renderer: Literal["auto", "default", "browser"] = "auto"

    @field_validator("url")
    @classmethod
    def url_must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("url must not be empty")
        return v.strip()


_MIN_USEFUL_CHARS = 200  # Content shorter than this after extraction is suspicious

# v2.7.0a1 M2 — Jina Reader endpoint. ``r.jina.ai/<url>`` returns clean
# markdown rendered by Jina's hosted browser-extract service. Free
# anonymous use; with ``JINA_API_KEY`` the rate limit climbs ~10x.
_JINA_READER_URL = "https://r.jina.ai/"


class JinaReaderError(Exception):
    """Raised when Jina Reader fails to extract content for a URL.

    Callers should catch this and fall back to local readability —
    Jina being unreachable, region-blocked, or rate-limiting must NOT
    break web_fetch for users without a paid extraction path.
    """


def fetch_via_jina_reader(
    url: str,
    *,
    api_key: str = "",
    timeout: float = 30.0,
) -> tuple[str, str, int]:
    """Fetch a URL via Jina Reader and return its markdown body.

    Jina Reader is a hosted browser-render-and-extract service. It
    handles JavaScript-rendered pages out of the box — a much better
    extraction backend than ``readability-lxml`` for SPAs / dynamic
    content / heavy frontends.

    Args:
        url: The URL to extract.
        api_key: Optional Jina API key. Anonymous mode is supported
            (just rate-limited more aggressively); pass ``""`` to use
            it.
        timeout: HTTP timeout in seconds.

    Returns:
        A ``(body, content_type, status_code)`` triple matching the
        shape returned by :meth:`WebFetchTool._fetch_with_httpx`. The
        body is markdown text and ``content_type`` is fixed at
        ``"text/markdown"`` so the downstream extractor can passthrough
        without re-parsing.

    Raises:
        JinaReaderError: Jina returned a non-200 status, was
            unreachable, or returned empty markdown — the caller
            should fall back to a local extractor.
    """
    target = f"{_JINA_READER_URL}{quote(url, safe=':/?&=#%')}"
    headers = {
        "Accept": "text/markdown",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        response = httpx.get(
            target,
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
        )
    except httpx.RequestError as exc:
        raise JinaReaderError(f"Jina Reader unreachable for {url}: {exc}") from exc

    if response.status_code == 429:
        raise JinaReaderError(f"Jina Reader rate limited for {url} (HTTP 429)")
    if response.status_code != 200:
        raise JinaReaderError(
            f"Jina Reader returned HTTP {response.status_code} for {url}"
        )
    body = response.text
    if not body or len(body.strip()) < _MIN_USEFUL_CHARS:
        # Jina occasionally returns an empty body for blocked / paywalled
        # pages; treat that as a failure so the caller can retry locally.
        raise JinaReaderError(f"Jina Reader returned empty body for {url}")
    return body, "text/markdown", response.status_code


async def fetch_via_jina_reader_async(
    url: str,
    *,
    api_key: str = "",
    timeout: float = 30.0,
) -> tuple[str, str, int]:
    """Async variant of :func:`fetch_via_jina_reader` using ``httpx.AsyncClient``."""
    target = f"{_JINA_READER_URL}{quote(url, safe=':/?&=#%')}"
    headers = {
        "Accept": "text/markdown",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            response = await client.get(target, headers=headers)
    except httpx.RequestError as exc:
        raise JinaReaderError(f"Jina Reader unreachable for {url}: {exc}") from exc

    if response.status_code == 429:
        raise JinaReaderError(f"Jina Reader rate limited for {url} (HTTP 429)")
    if response.status_code != 200:
        raise JinaReaderError(
            f"Jina Reader returned HTTP {response.status_code} for {url}"
        )
    body = response.text
    if not body or len(body.strip()) < _MIN_USEFUL_CHARS:
        raise JinaReaderError(f"Jina Reader returned empty body for {url}")
    return body, "text/markdown", response.status_code


def _resolve_jina_api_key(cfg: object | None) -> str:
    """Look up the Jina API key from ``WebFetchConfig`` env var setting."""
    env_var = "JINA_API_KEY"
    if cfg is not None:
        env_var = getattr(cfg, "jina_api_key_env", "JINA_API_KEY") or "JINA_API_KEY"
    return os.environ.get(env_var, "")


def _resolve_extraction_backend(cfg: object | None) -> str:
    """Resolve the configured extraction backend string.

    Returns one of ``"auto"``, ``"jina"``, ``"local"``. ``"auto"``
    means "try Jina first, fall back to local on any failure".
    """
    if cfg is None:
        return "auto"
    value = getattr(cfg, "extraction_backend", "auto") or "auto"
    if value not in ("auto", "jina", "local"):
        # Defensive: unknown values fall back to the safe "auto" mode
        # rather than raising. A user typo in settings.json shouldn't
        # break web_fetch.
        logger.warning(
            "WebFetchConfig.extraction_backend=%r is unknown — using 'auto'", value,
        )
        return "auto"
    return value


def _playwright_available() -> bool:
    """Return True if playwright is installed."""
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def _looks_unrendered(content: str) -> bool:
    """Heuristic: return True if extracted content looks like an unrendered JS page.

    Indicators: very short content, or dominated by JS framework boilerplate.
    """
    stripped = content.strip()
    if len(stripped) < _MIN_USEFUL_CHARS:
        return True
    # Check for common SPA/JS-only markers in the extracted text
    js_markers = ("__NEXT_DATA__", "window.__", "noscript", "enable JavaScript")
    marker_count = sum(1 for m in js_markers if m in stripped)
    return marker_count >= 2


# Module-level cache shared across tool instances
_cache = UrlCache(max_entries=50, ttl=900.0)


class WebFetchTool(Tool):
    """Tool that fetches and extracts content from a URL.

    **M5 note:** now async-native via :meth:`execute_async`. The legacy
    :meth:`execute` is preserved (it uses ``httpx`` sync API) so that
    sync-only call sites keep working, but the engine's
    :class:`~llm_code.engine.components.tool_executor.ToolExecutorComponent`
    awaits ``execute_async`` when available — that path uses
    :class:`httpx.AsyncClient` and does not block the event loop.
    """

    # Marks this tool as cooperatively async — the engine skips the
    # to_thread bridge and awaits execute_async() directly.
    is_async: bool = True

    def __init__(self, cache: UrlCache | None = None) -> None:
        self._cache = cache if cache is not None else _cache

    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def description(self) -> str:
        return (
            "Fetch content from a URL and return it in a readable format. "
            "Supports HTML (converted to markdown), JSON (pretty-printed), "
            "and plain text. Results are cached for 15 minutes."
        )

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch.",
                },
                "prompt": {
                    "type": "string",
                    "description": "Optional description of what to extract from the page.",
                    "default": "",
                },
                "max_length": {
                    "type": "integer",
                    "description": "Maximum content length in characters.",
                    "default": 50_000,
                },
                "raw": {
                    "type": "boolean",
                    "description": "If true, skip readability processing for HTML.",
                    "default": False,
                },
                "renderer": {
                    "type": "string",
                    "enum": ["auto", "default", "browser"],
                    "description": (
                        "Renderer to use: 'auto' (detect playwright availability), "
                        "'default' (httpx), 'browser' (playwright)."
                    ),
                    "default": "auto",
                },
            },
            "required": ["url"],
        }

    @property
    def input_model(self) -> type[BaseModel]:
        return WebFetchInput

    def is_read_only(self, args: dict) -> bool:  # noqa: ARG002
        # Network read — does not mutate local filesystem.
        return True

    def is_concurrency_safe(self, args: dict) -> bool:  # noqa: ARG002
        return True

    def _resolve_renderer(self, renderer: str) -> str:
        """Resolve 'auto' to a concrete renderer based on availability."""
        if renderer == "auto":
            try:
                import playwright  # noqa: F401
                return "browser"
            except ImportError:
                return "default"
        return renderer

    def _get_web_fetch_config(self) -> object | None:
        """Attempt to load :class:`WebFetchConfig` from runtime config.

        Mirrors :meth:`WebSearchTool._get_web_search_config`. Returns
        the dataclass instance or ``None`` if the runtime config module
        is unavailable.
        """
        try:
            from llm_code.runtime.config import WebFetchConfig
            return WebFetchConfig()
        except ImportError:
            return None

    def _try_jina_extraction(
        self,
        url: str,
        cfg: object | None,
    ) -> tuple[str, str, int] | None:
        """Try Jina Reader extraction for ``url``.

        Returns ``(body, content_type, status_code)`` on success or
        ``None`` if the configured extraction backend disables Jina
        OR if Jina fails — the caller falls back to the local
        readability path.
        """
        backend = _resolve_extraction_backend(cfg)
        if backend == "local":
            return None
        api_key = _resolve_jina_api_key(cfg)
        try:
            return fetch_via_jina_reader(url, api_key=api_key)
        except JinaReaderError as exc:
            if backend == "jina":
                # Strict mode — re-raise so the caller surfaces the error.
                logger.warning("Jina-only extraction failed for %s: %s", url, exc)
                raise
            logger.info(
                "Jina Reader failed for %s, falling back to local: %s", url, exc,
            )
            return None

    async def _try_jina_extraction_async(
        self,
        url: str,
        cfg: object | None,
    ) -> tuple[str, str, int] | None:
        """Async variant of :meth:`_try_jina_extraction`."""
        backend = _resolve_extraction_backend(cfg)
        if backend == "local":
            return None
        api_key = _resolve_jina_api_key(cfg)
        try:
            return await fetch_via_jina_reader_async(url, api_key=api_key)
        except JinaReaderError as exc:
            if backend == "jina":
                logger.warning("Jina-only extraction failed for %s: %s", url, exc)
                raise
            logger.info(
                "Jina Reader failed for %s, falling back to local: %s", url, exc,
            )
            return None

    def _fetch_with_httpx(self, url: str) -> tuple[str, str, int]:
        """Fetch URL with httpx. Returns (body, content_type, status_code)."""
        response = httpx.get(url, follow_redirects=True, timeout=30.0)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "text/plain")
        return response.text, content_type, response.status_code

    def _fetch_with_browser(self, url: str) -> tuple[str, str, int]:
        """Fetch URL with playwright. Falls back to httpx if playwright fails."""
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    response = page.goto(url, timeout=30_000)
                    status_code = response.status if response else 200
                    content = page.content()
                    return content, "text/html", status_code
                finally:
                    browser.close()
        except Exception:
            # Fall back to httpx
            return self._fetch_with_httpx(url)

    def execute(self, args: dict) -> ToolResult:
        """Execute the web fetch tool."""
        # Validate and parse input
        try:
            parsed = WebFetchInput(**args)
        except (KeyError, TypeError, Exception) as exc:
            return ToolResult(
                output=f"Invalid input: {exc}",
                is_error=True,
            )

        url = parsed.url
        max_length = parsed.max_length
        raw = parsed.raw
        renderer = parsed.renderer

        # Check URL safety
        safety = classify_url(url)
        if safety.is_blocked:
            reasons = ", ".join(safety.reasons) if safety.reasons else "unknown"
            return ToolResult(
                output=f"URL blocked: {url} (reasons: {reasons})",
                is_error=True,
                metadata={"url": url, "blocked": True, "reasons": list(safety.reasons)},
            )

        # Check cache
        cached_content = self._cache.get(url)
        if cached_content is not None:
            return ToolResult(
                output=cached_content,
                is_error=False,
                metadata={
                    "url": url,
                    "cached": True,
                    "status_code": None,
                    "content_type": None,
                },
            )

        # Resolve renderer
        resolved_renderer = self._resolve_renderer(renderer)

        # v2.7.0a1 M2 — Jina Reader extraction path.
        # When configured (extraction_backend == "auto" or "jina"), try
        # Jina first. Jina handles JS rendering itself, so we skip the
        # local renderer when Jina succeeds. ``raw=True`` callers and
        # the explicit ``renderer=browser`` request bypass Jina —
        # those are deliberate "give me the raw bytes" / "drive a
        # local browser" requests, not "give me clean markdown".
        cfg = self._get_web_fetch_config()
        body: str | None = None
        content_type: str | None = None
        status_code: int | None = None
        used_jina = False
        if not raw and resolved_renderer != "browser":
            try:
                jina_result = self._try_jina_extraction(url, cfg)
            except JinaReaderError as exc:
                # Strict ``extraction_backend == "jina"`` mode.
                return ToolResult(
                    output=f"Jina Reader extraction failed for {url}: {exc}",
                    is_error=True,
                    metadata={"url": url, "cached": False},
                )
            if jina_result is not None:
                body, content_type, status_code = jina_result
                used_jina = True

        # Local fetch (fallback path or explicit raw / browser request).
        if body is None:
            try:
                if resolved_renderer == "browser":
                    body, content_type, status_code = self._fetch_with_browser(url)
                else:
                    body, content_type, status_code = self._fetch_with_httpx(url)
            except httpx.HTTPStatusError as exc:
                return ToolResult(
                    output=f"HTTP error {exc.response.status_code}: {exc}",
                    is_error=True,
                    metadata={
                        "url": url,
                        "status_code": exc.response.status_code,
                        "cached": False,
                    },
                )
            except httpx.RequestError as exc:
                return ToolResult(
                    output=f"Network error fetching {url}: {exc}",
                    is_error=True,
                    metadata={"url": url, "cached": False},
                )
            except Exception as exc:
                return ToolResult(
                    output=f"Error fetching {url}: {exc}",
                    is_error=True,
                    metadata={"url": url, "cached": False},
                )

        # Extract content (Jina output is already markdown — extract_content
        # passes "text/markdown" through unchanged, so this is safe).
        content = extract_content(body, content_type, raw=raw, max_length=max_length)

        # Auto-retry with browser if local httpx result looks like
        # unrendered JS. Jina already handles JS rendering — skip this
        # retry when we used it.
        if (
            not used_jina
            and resolved_renderer == "default"
            and "html" in content_type
            and _looks_unrendered(content)
            and _playwright_available()
        ):
            try:
                body2, ct2, sc2 = self._fetch_with_browser(url)
                content2 = extract_content(body2, ct2, raw=raw, max_length=max_length)
                if len(content2) > len(content):
                    content = content2
                    status_code = sc2
                    content_type = ct2
            except Exception:
                pass  # keep original content

        # Cache result
        self._cache.put(url, content)

        return ToolResult(
            output=content,
            is_error=False,
            metadata={
                "url": url,
                "status_code": status_code,
                "content_type": content_type,
                "cached": False,
                "extraction_backend": "jina" if used_jina else "local",
            },
        )

    # ------------------------------------------------------------------
    # M5 async-native execution
    # ------------------------------------------------------------------

    async def _fetch_with_httpx_async(self, url: str) -> tuple[str, str, int]:
        """Async variant of :meth:`_fetch_with_httpx` using :class:`httpx.AsyncClient`."""
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "text/plain")
            return response.text, content_type, response.status_code

    async def execute_async(self, args: dict) -> ToolResult:
        """Async-native fetch using :class:`httpx.AsyncClient`.

        Mirrors :meth:`execute` but never blocks the event loop. The
        browser fallback path still calls sync Playwright on a thread —
        Playwright's async API is a different import surface and
        re-wiring that is out of scope for M5.
        """
        # Validate input (same as sync path).
        try:
            parsed = WebFetchInput(**args)
        except (KeyError, TypeError, Exception) as exc:  # noqa: BLE001
            return ToolResult(output=f"Invalid input: {exc}", is_error=True)

        url = parsed.url
        max_length = parsed.max_length
        raw = parsed.raw
        renderer = parsed.renderer

        safety = classify_url(url)
        if safety.is_blocked:
            reasons = ", ".join(safety.reasons) if safety.reasons else "unknown"
            return ToolResult(
                output=f"URL blocked: {url} (reasons: {reasons})",
                is_error=True,
                metadata={"url": url, "blocked": True, "reasons": list(safety.reasons)},
            )

        cached_content = self._cache.get(url)
        if cached_content is not None:
            return ToolResult(
                output=cached_content,
                is_error=False,
                metadata={
                    "url": url,
                    "cached": True,
                    "status_code": None,
                    "content_type": None,
                },
            )

        resolved_renderer = self._resolve_renderer(renderer)
        import asyncio as _asyncio

        # v2.7.0a1 M2 — Jina Reader async extraction path.
        cfg = self._get_web_fetch_config()
        body: str | None = None
        content_type: str | None = None
        status_code: int | None = None
        used_jina = False
        if not raw and resolved_renderer != "browser":
            try:
                jina_result = await self._try_jina_extraction_async(url, cfg)
            except JinaReaderError as exc:
                return ToolResult(
                    output=f"Jina Reader extraction failed for {url}: {exc}",
                    is_error=True,
                    metadata={"url": url, "cached": False},
                )
            if jina_result is not None:
                body, content_type, status_code = jina_result
                used_jina = True

        if body is None:
            try:
                if resolved_renderer == "browser":
                    # Playwright sync API → run in thread; rewriting to the
                    # async Playwright surface is out of scope for M5.
                    body, content_type, status_code = await _asyncio.to_thread(
                        self._fetch_with_browser, url
                    )
                else:
                    body, content_type, status_code = await self._fetch_with_httpx_async(url)
            except httpx.HTTPStatusError as exc:
                return ToolResult(
                    output=f"HTTP error {exc.response.status_code}: {exc}",
                    is_error=True,
                    metadata={
                        "url": url,
                        "status_code": exc.response.status_code,
                        "cached": False,
                    },
                )
            except httpx.RequestError as exc:
                return ToolResult(
                    output=f"Network error fetching {url}: {exc}",
                    is_error=True,
                    metadata={"url": url, "cached": False},
                )
            except Exception as exc:  # noqa: BLE001
                return ToolResult(
                    output=f"Error fetching {url}: {exc}",
                    is_error=True,
                    metadata={"url": url, "cached": False},
                )

        content = extract_content(body, content_type, raw=raw, max_length=max_length)

        if (
            not used_jina
            and resolved_renderer == "default"
            and "html" in content_type
            and _looks_unrendered(content)
            and _playwright_available()
        ):
            try:
                body2, ct2, sc2 = await _asyncio.to_thread(self._fetch_with_browser, url)
                content2 = extract_content(body2, ct2, raw=raw, max_length=max_length)
                if len(content2) > len(content):
                    content = content2
                    status_code = sc2
                    content_type = ct2
            except Exception:
                pass

        self._cache.put(url, content)

        return ToolResult(
            output=content,
            is_error=False,
            metadata={
                "url": url,
                "status_code": status_code,
                "content_type": content_type,
                "cached": False,
                "extraction_backend": "jina" if used_jina else "local",
            },
        )
