"""WebFetch tool — fetch and extract content from URLs."""
from __future__ import annotations

from typing import Literal

import httpx
from pydantic import BaseModel, field_validator

from llm_code.tools.base import PermissionLevel, Tool, ToolResult
from llm_code.tools.web_common import UrlCache, classify_url, extract_content


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
    """Tool that fetches and extracts content from a URL."""

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
        return False

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

        # Fetch content
        try:
            if resolved_renderer == "browser":
                body, content_type, status_code = self._fetch_with_browser(url)
            else:
                body, content_type, status_code = self._fetch_with_httpx(url)
        except httpx.HTTPStatusError as exc:
            return ToolResult(
                output=f"HTTP error {exc.response.status_code}: {exc}",
                is_error=True,
                metadata={"url": url, "status_code": exc.response.status_code, "cached": False},
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

        # Extract content
        content = extract_content(body, content_type, raw=raw, max_length=max_length)

        # Auto-retry with browser if httpx result looks like unrendered JS
        if (
            resolved_renderer == "default"
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
            },
        )
