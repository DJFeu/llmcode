"""Shared utilities for web tools (URL safety, caching, extraction)."""
from __future__ import annotations

import dataclasses
import ipaddress
import json
import re
import time
from collections import OrderedDict
from urllib.parse import urlparse


@dataclasses.dataclass(frozen=True)
class UrlSafetyResult:
    """Result of URL safety classification."""

    classification: str
    reasons: tuple[str, ...] = ()

    @property
    def is_safe(self) -> bool:
        """Return True if URL is safe to fetch."""
        return self.classification == "safe"

    @property
    def is_blocked(self) -> bool:
        """Return True if URL should be blocked."""
        return self.classification == "blocked"

    @property
    def needs_confirm(self) -> bool:
        """Return True if URL needs user confirmation."""
        return self.classification == "needs_confirm"


def classify_url(url: str) -> UrlSafetyResult:
    """Classify URL as safe, needs_confirm, or blocked.

    Rules:
    - blocked: file://, private IPs, cloud metadata, invalid URLs, unsupported schemes
    - needs_confirm: localhost, 127.0.0.1, IP-only URLs, non-standard ports
    - safe: standard HTTP/HTTPS URLs to regular hosts
    """
    reasons: list[str] = []

    try:
        parsed = urlparse(url)
    except Exception:
        return UrlSafetyResult(classification="blocked", reasons=("invalid URL",))

    # Check scheme
    if not parsed.scheme:
        return UrlSafetyResult(classification="blocked", reasons=("missing scheme",))

    if parsed.scheme == "file":
        return UrlSafetyResult(
            classification="blocked", reasons=("file scheme",)
        )

    if parsed.scheme not in ("http", "https"):
        return UrlSafetyResult(
            classification="blocked", reasons=("unsupported scheme",)
        )

    # Check host
    if not parsed.hostname:
        return UrlSafetyResult(classification="blocked", reasons=("missing host",))

    host = parsed.hostname

    # Check for cloud metadata hosts (must be before IP check)
    if host in ("169.254.169.254", "metadata.google.internal", "metadata.azure.com"):
        return UrlSafetyResult(
            classification="blocked", reasons=("metadata",)
        )

    # Try to parse as IP address
    is_ip = False
    try:
        ip = ipaddress.ip_address(host)
        is_ip = True

        # Check for loopback IPv6 (blocked)
        if ip.version == 6 and ip.is_loopback:
            return UrlSafetyResult(
                classification="blocked", reasons=("loopback",)
            )

        # Check for loopback IPv4 first (needs confirm)
        if ip.is_loopback:
            return UrlSafetyResult(
                classification="needs_confirm", reasons=("127.0.0.1",)
            )

        # Check for private IPs (blocked)
        if ip.is_private:
            return UrlSafetyResult(
                classification="blocked", reasons=("private IP",)
            )
    except ValueError:
        # Not an IP address, check for localhost string
        pass

    # Check for localhost name (needs confirm)
    if host == "localhost":
        return UrlSafetyResult(
            classification="needs_confirm", reasons=("localhost",)
        )

    # Check port
    port = parsed.port
    if is_ip and port is None:
        # IP-only URL without port (needs confirm)
        return UrlSafetyResult(
            classification="needs_confirm", reasons=("IP-only",)
        )

    if port is not None and port not in (80, 443):
        # Non-standard port (needs confirm)
        return UrlSafetyResult(
            classification="needs_confirm", reasons=("non-standard port",)
        )

    # All checks passed
    return UrlSafetyResult(classification="safe", reasons=())


@dataclasses.dataclass(frozen=True)
class CacheEntry:
    """Cache entry with TTL support."""

    content: str
    fetched_at: float
    ttl: float = 900.0

    @property
    def is_expired(self) -> bool:
        """Return True if entry has expired based on TTL."""
        return time.time() - self.fetched_at > self.ttl


class UrlCache:
    """LRU cache for URL content with TTL support."""

    def __init__(self, max_entries: int = 50, ttl: float = 900.0) -> None:
        """Initialize cache.

        Args:
            max_entries: Maximum number of entries before evicting oldest.
            ttl: Time-to-live for entries in seconds.
        """
        self.max_entries = max_entries
        self.ttl = ttl
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()

    def get(self, url: str) -> str | None:
        """Get cached content for URL, return None if not found or expired."""
        if url not in self._cache:
            return None

        entry = self._cache[url]
        if entry.is_expired:
            del self._cache[url]
            return None

        # Move to end (LRU)
        self._cache.move_to_end(url)
        return entry.content

    def put(self, url: str, content: str) -> None:
        """Store content in cache. Evicts oldest entry if cache is full."""
        # If updating existing, remove it first
        if url in self._cache:
            del self._cache[url]

        # Add new entry
        entry = CacheEntry(content=content, fetched_at=time.time(), ttl=self.ttl)
        self._cache[url] = entry

        # Evict oldest if over capacity
        if len(self._cache) > self.max_entries:
            self._cache.popitem(last=False)

    def clear(self) -> None:
        """Clear all cached entries."""
        self._cache.clear()


def _html_to_markdown(html: str, use_readability: bool = True) -> str:
    """Convert HTML to markdown using readability and html2text.

    Falls back gracefully if dependencies are missing.
    """
    content = html

    # Try readability first if requested
    if use_readability:
        try:
            from readability import Document
            doc = Document(content)
            content = doc.summary()
        except ImportError:
            pass

    # Try html2text
    try:
        import html2text
        converter = html2text.HTML2Text()
        converter.ignore_links = False
        return converter.handle(content)
    except ImportError:
        # Fallback: simple regex tag stripping
        content = re.sub(r"<[^>]+>", "", content)
        content = re.sub(r"\s+", " ", content).strip()
        return content


def extract_content(
    body: str,
    content_type: str,
    raw: bool = False,
    max_length: int = 50000,
) -> str:
    """Extract and format content based on content type.

    Args:
        body: Raw content body.
        content_type: MIME type of content.
        raw: If True, skip readability for HTML (just strip tags).
        max_length: Maximum length before truncation.

    Returns:
        Formatted content, possibly truncated with "[truncated]" marker.
    """
    if not body:
        return ""

    result = ""

    if content_type.startswith("application/json"):
        try:
            data = json.loads(body)
            result = json.dumps(data, indent=2)
        except json.JSONDecodeError:
            result = body
    elif "html" in content_type:
        result = _html_to_markdown(body, use_readability=not raw)
    else:
        # Passthrough
        result = body

    # Truncate if needed
    if len(result) > max_length:
        truncated_marker = "\n\n[truncated]"
        available = max_length - len(truncated_marker)
        result = result[:available] + truncated_marker

    return result
