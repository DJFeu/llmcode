"""Tests for web tools common utilities (URL safety, caching, extraction)."""
from __future__ import annotations

import json
import time

import pytest

from llm_code.tools.web_common import (
    CacheEntry,
    UrlCache,
    UrlSafetyResult,
    classify_url,
    extract_content,
)


class TestUrlSafetyResult:
    """Test UrlSafetyResult dataclass."""

    def test_safe_result_has_is_safe_property(self) -> None:
        result = UrlSafetyResult(classification="safe", reasons=())
        assert result.is_safe is True

    def test_safe_result_has_is_blocked_false(self) -> None:
        result = UrlSafetyResult(classification="safe", reasons=())
        assert result.is_blocked is False

    def test_safe_result_has_needs_confirm_false(self) -> None:
        result = UrlSafetyResult(classification="safe", reasons=())
        assert result.needs_confirm is False

    def test_blocked_result_properties(self) -> None:
        result = UrlSafetyResult(
            classification="blocked", reasons=("file scheme",)
        )
        assert result.is_blocked is True
        assert result.is_safe is False
        assert result.needs_confirm is False

    def test_needs_confirm_result_properties(self) -> None:
        result = UrlSafetyResult(
            classification="needs_confirm", reasons=("localhost",)
        )
        assert result.needs_confirm is True
        assert result.is_safe is False
        assert result.is_blocked is False

    def test_result_is_frozen(self) -> None:
        result = UrlSafetyResult(classification="safe", reasons=())
        with pytest.raises(AttributeError):
            result.classification = "blocked"  # type: ignore


class TestClassifyUrl:
    """Test URL classification logic."""

    # Safe URLs
    def test_safe_https_url(self) -> None:
        result = classify_url("https://example.com/path")
        assert result.is_safe

    def test_safe_http_url(self) -> None:
        result = classify_url("http://example.com/path")
        assert result.is_safe

    # Blocked: file scheme
    def test_blocked_file_scheme(self) -> None:
        result = classify_url("file:///etc/passwd")
        assert result.is_blocked
        assert "file scheme" in result.reasons

    # Blocked: private IPs
    def test_blocked_private_ip_10_range(self) -> None:
        result = classify_url("http://10.0.0.1")
        assert result.is_blocked
        assert "private IP" in result.reasons

    def test_blocked_private_ip_172_range(self) -> None:
        result = classify_url("http://172.16.0.1")
        assert result.is_blocked
        assert "private IP" in result.reasons

    def test_blocked_private_ip_192_range(self) -> None:
        result = classify_url("http://192.168.1.1")
        assert result.is_blocked
        assert "private IP" in result.reasons

    # Blocked: loopback IPv6
    def test_blocked_loopback_ipv6(self) -> None:
        result = classify_url("http://[::1]")
        assert result.is_blocked
        assert "loopback" in result.reasons

    # Blocked: cloud metadata
    def test_blocked_aws_metadata(self) -> None:
        result = classify_url("http://169.254.169.254")
        assert result.is_blocked
        assert "metadata" in result.reasons

    def test_blocked_google_metadata(self) -> None:
        result = classify_url("http://metadata.google.internal")
        assert result.is_blocked
        assert "metadata" in result.reasons

    def test_blocked_azure_metadata(self) -> None:
        result = classify_url("http://metadata.azure.com")
        assert result.is_blocked
        assert "metadata" in result.reasons

    # Needs confirm: localhost
    def test_needs_confirm_localhost(self) -> None:
        result = classify_url("http://localhost:8080")
        assert result.needs_confirm
        assert "localhost" in result.reasons

    def test_needs_confirm_127_0_0_1(self) -> None:
        result = classify_url("http://127.0.0.1")
        assert result.needs_confirm
        assert "127.0.0.1" in result.reasons

    # Needs confirm: IP-only URLs
    def test_needs_confirm_ip_only_url(self) -> None:
        result = classify_url("http://8.8.8.8")
        assert result.needs_confirm
        assert "IP-only" in result.reasons

    # Needs confirm: non-standard ports
    def test_needs_confirm_nonstandard_port(self) -> None:
        result = classify_url("http://example.com:8080")
        assert result.needs_confirm
        assert "non-standard port" in result.reasons

    def test_needs_confirm_port_3000(self) -> None:
        result = classify_url("http://localhost:3000")
        assert result.needs_confirm

    # Safe standard ports
    def test_safe_port_80(self) -> None:
        result = classify_url("http://example.com:80")
        assert result.is_safe

    def test_safe_port_443(self) -> None:
        result = classify_url("https://example.com:443")
        assert result.is_safe

    # Invalid URLs
    def test_invalid_url_no_scheme(self) -> None:
        result = classify_url("example.com")
        assert result.is_blocked

    def test_invalid_url_no_host(self) -> None:
        result = classify_url("http://")
        assert result.is_blocked


class TestCacheEntry:
    """Test CacheEntry dataclass."""

    def test_cache_entry_is_frozen(self) -> None:
        entry = CacheEntry(content="test", fetched_at=time.time())
        with pytest.raises(AttributeError):
            entry.content = "modified"  # type: ignore

    def test_cache_entry_is_not_expired_within_ttl(self) -> None:
        now = time.time()
        entry = CacheEntry(content="test", fetched_at=now, ttl=900.0)
        assert entry.is_expired is False

    def test_cache_entry_is_expired_after_ttl(self) -> None:
        past = time.time() - 1000  # 1000 seconds ago
        entry = CacheEntry(content="test", fetched_at=past, ttl=900.0)
        assert entry.is_expired is True

    def test_cache_entry_default_ttl(self) -> None:
        now = time.time()
        entry = CacheEntry(content="test", fetched_at=now)
        assert entry.ttl == 900.0

    def test_cache_entry_zero_ttl_is_immediately_expired(self) -> None:
        entry = CacheEntry(content="test", fetched_at=time.time(), ttl=0.0)
        assert entry.is_expired is True


class TestUrlCache:
    """Test UrlCache class."""

    def test_cache_get_miss(self) -> None:
        cache = UrlCache()
        result = cache.get("http://example.com")
        assert result is None

    def test_cache_put_and_get(self) -> None:
        cache = UrlCache()
        cache.put("http://example.com", "content")
        result = cache.get("http://example.com")
        assert result == "content"

    def test_cache_ttl_expiry(self) -> None:
        cache = UrlCache(ttl=0.0)
        cache.put("http://example.com", "content")
        result = cache.get("http://example.com")
        assert result is None

    def test_cache_max_entries_eviction(self) -> None:
        cache = UrlCache(max_entries=2)
        cache.put("http://url1.com", "content1")
        cache.put("http://url2.com", "content2")
        cache.put("http://url3.com", "content3")
        # Oldest (url1) should be evicted
        assert cache.get("http://url1.com") is None
        assert cache.get("http://url2.com") == "content2"
        assert cache.get("http://url3.com") == "content3"

    def test_cache_put_updates_existing(self) -> None:
        cache = UrlCache()
        cache.put("http://example.com", "content1")
        cache.put("http://example.com", "content2")
        result = cache.get("http://example.com")
        assert result == "content2"

    def test_cache_clear(self) -> None:
        cache = UrlCache()
        cache.put("http://example.com", "content")
        cache.clear()
        result = cache.get("http://example.com")
        assert result is None


class TestExtractContent:
    """Test HTML content extraction."""

    def test_extract_html_to_markdown(self) -> None:
        html = "<h1>Title</h1><p>Paragraph</p>"
        result = extract_content(html, "text/html")
        assert isinstance(result, str)
        assert len(result) > 0
        assert "Title" in result or "title" in result.lower()

    def test_extract_html_raw_mode(self) -> None:
        html = "<h1>Title</h1><p>Paragraph</p>"
        result = extract_content(html, "text/html", raw=True)
        assert isinstance(result, str)
        # Raw mode should strip tags but not use readability

    def test_extract_json_formatting(self) -> None:
        json_data = {"key": "value", "nested": {"inner": "data"}}
        json_str = json.dumps(json_data)
        result = extract_content(json_str, "application/json")
        assert "key" in result
        assert "value" in result
        # Should be formatted with indentation
        assert "\n" in result

    def test_extract_plain_text_passthrough(self) -> None:
        text = "This is plain text"
        result = extract_content(text, "text/plain")
        assert result == text

    def test_extract_truncation(self) -> None:
        long_content = "x" * 60000
        result = extract_content(long_content, "text/plain", max_length=50000)
        assert len(result) <= 50000
        assert "[truncated]" in result

    def test_extract_empty_content(self) -> None:
        result = extract_content("", "text/plain")
        assert result == ""
