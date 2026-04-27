"""Firecrawl web-fetch backend — Playwright-rendered markdown.

Free tier: 500 calls / month. Auth via ``FIRECRAWL_API_KEY`` env var.
Docs: https://docs.firecrawl.dev/

Why Firecrawl?
--------------

Some SPAs return empty markdown even from Jina Reader (rare; Jina
handles 90%+). Firecrawl runs Playwright in their cloud and returns
rendered markdown — a robust fallback for the long tail of
JavaScript-only pages without forcing every user to install
Playwright locally.

Position in the chain
---------------------

Used as the THIRD fallback in ``web_fetch``'s ``auto`` mode:

1. Jina Reader (v2.7.0a1) — first try.
2. Local readability + httpx (v2.6.x) — second try.
3. Firecrawl (v2.8.0 M6) — only if both above returned <200 chars
   AND ``FIRECRAWL_API_KEY`` is set.

Without the env var the path is silently skipped — preserves v2.7.0
behaviour for users without the key.

Plan: docs/superpowers/plans/2026-04-27-llm-code-v17-m6-firecrawl.md
"""
from __future__ import annotations

import dataclasses
import logging
import os

import httpx

logger = logging.getLogger(__name__)

_FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v1/scrape"
_MIN_USEFUL_CHARS = 200


@dataclasses.dataclass(frozen=True)
class ExtractedContent:
    """Result of a Firecrawl scrape.

    Mirrors the shape that ``web_fetch._try_jina_extraction`` returns
    so the caller can swap implementations without branching.
    """

    text: str
    title: str
    status_code: int


class FirecrawlError(Exception):
    """Base class for Firecrawl extraction failures."""


class FirecrawlRateLimitError(FirecrawlError):
    """Firecrawl returned HTTP 429."""


class FirecrawlAuthError(FirecrawlError):
    """Firecrawl returned HTTP 401 / 403."""


class FirecrawlEmptyResultError(FirecrawlError):
    """Firecrawl returned no useful markdown (caller should fall through)."""


def _resolve_api_key(api_key: str | None) -> str:
    """Resolve the API key from arg or canonical env var.

    Returns an empty string if neither source is set; the caller
    surfaces this as an :class:`FirecrawlAuthError` so the chain
    falls through silently when the user simply hasn't opted in.
    """
    if api_key:
        return api_key.strip()
    return os.environ.get("FIRECRAWL_API_KEY", "").strip()


def fetch_via_firecrawl(
    url: str,
    *,
    timeout: float = 30.0,
    api_key: str | None = None,
) -> ExtractedContent:
    """Fetch ``url`` via Firecrawl's ``/v1/scrape`` endpoint.

    Args:
        url: Target URL.
        timeout: HTTP timeout in seconds.
        api_key: Optional explicit API key. Defaults to
            ``FIRECRAWL_API_KEY`` env var.

    Returns:
        :class:`ExtractedContent` on success.

    Raises:
        FirecrawlAuthError: API key missing / 401 / 403.
        FirecrawlRateLimitError: HTTP 429.
        FirecrawlEmptyResultError: Successful response but no useful
            markdown — caller should fall through.
        FirecrawlError: Any other failure (transport, parse, non-200).
    """
    key = _resolve_api_key(api_key)
    if not key:
        raise FirecrawlAuthError(
            "Firecrawl API key not configured — set FIRECRAWL_API_KEY"
        )

    try:
        response = httpx.post(
            _FIRECRAWL_SCRAPE_URL,
            json={
                "url": url,
                "formats": ["markdown"],
            },
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=timeout,
        )
    except httpx.RequestError as exc:
        raise FirecrawlError(f"Firecrawl unreachable for {url}: {exc}") from exc

    if response.status_code == 429:
        raise FirecrawlRateLimitError(
            f"Firecrawl rate limited for {url} (HTTP 429)"
        )
    if response.status_code in (401, 403):
        raise FirecrawlAuthError(
            f"Firecrawl auth failed (HTTP {response.status_code}) — "
            "check FIRECRAWL_API_KEY"
        )
    if response.status_code != 200:
        raise FirecrawlError(
            f"Firecrawl returned HTTP {response.status_code} for {url}"
        )

    try:
        data = response.json()
    except Exception as exc:
        raise FirecrawlError(f"Firecrawl parse error for {url}: {exc}") from exc

    if not isinstance(data, dict):
        raise FirecrawlError(f"Firecrawl returned non-dict body for {url}")

    body_root = data.get("data") if isinstance(data.get("data"), dict) else data
    markdown = body_root.get("markdown") or ""
    if not isinstance(markdown, str):
        markdown = str(markdown)
    metadata = body_root.get("metadata") if isinstance(body_root.get("metadata"), dict) else {}
    title = metadata.get("title") or ""
    if not isinstance(title, str):
        title = str(title)

    if not markdown or len(markdown.strip()) < _MIN_USEFUL_CHARS:
        raise FirecrawlEmptyResultError(
            f"Firecrawl returned empty / insufficient markdown for {url}"
        )

    return ExtractedContent(
        text=markdown,
        title=title,
        status_code=response.status_code,
    )


async def fetch_via_firecrawl_async(
    url: str,
    *,
    timeout: float = 30.0,
    api_key: str | None = None,
) -> ExtractedContent:
    """Async variant of :func:`fetch_via_firecrawl`."""
    key = _resolve_api_key(api_key)
    if not key:
        raise FirecrawlAuthError(
            "Firecrawl API key not configured — set FIRECRAWL_API_KEY"
        )

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                _FIRECRAWL_SCRAPE_URL,
                json={
                    "url": url,
                    "formats": ["markdown"],
                },
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
    except httpx.RequestError as exc:
        raise FirecrawlError(f"Firecrawl unreachable for {url}: {exc}") from exc

    if response.status_code == 429:
        raise FirecrawlRateLimitError(
            f"Firecrawl rate limited for {url} (HTTP 429)"
        )
    if response.status_code in (401, 403):
        raise FirecrawlAuthError(
            f"Firecrawl auth failed (HTTP {response.status_code}) — "
            "check FIRECRAWL_API_KEY"
        )
    if response.status_code != 200:
        raise FirecrawlError(
            f"Firecrawl returned HTTP {response.status_code} for {url}"
        )

    try:
        data = response.json()
    except Exception as exc:
        raise FirecrawlError(f"Firecrawl parse error for {url}: {exc}") from exc

    if not isinstance(data, dict):
        raise FirecrawlError(f"Firecrawl returned non-dict body for {url}")

    body_root = data.get("data") if isinstance(data.get("data"), dict) else data
    markdown = body_root.get("markdown") or ""
    if not isinstance(markdown, str):
        markdown = str(markdown)
    metadata = body_root.get("metadata") if isinstance(body_root.get("metadata"), dict) else {}
    title = metadata.get("title") or ""
    if not isinstance(title, str):
        title = str(title)

    if not markdown or len(markdown.strip()) < _MIN_USEFUL_CHARS:
        raise FirecrawlEmptyResultError(
            f"Firecrawl returned empty / insufficient markdown for {url}"
        )

    return ExtractedContent(
        text=markdown,
        title=title,
        status_code=response.status_code,
    )
