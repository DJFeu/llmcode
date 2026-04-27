"""Firecrawl web_fetch backend tests (v2.8.0 M6)."""
from __future__ import annotations

import json as _json

import httpx
import pytest
import respx

from llm_code.tools.web_fetch_backends.firecrawl import (
    ExtractedContent,
    FirecrawlAuthError,
    FirecrawlEmptyResultError,
    FirecrawlError,
    FirecrawlRateLimitError,
    fetch_via_firecrawl,
)

FIRECRAWL_URL = "https://api.firecrawl.dev/v1/scrape"


class TestFirecrawlAuth:
    def test_missing_key_raises_auth_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
        with pytest.raises(FirecrawlAuthError, match="FIRECRAWL_API_KEY"):
            fetch_via_firecrawl("https://example.com")

    def test_explicit_api_key_used(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
        with respx.mock:
            route = respx.post(FIRECRAWL_URL).mock(
                return_value=httpx.Response(200, json={
                    "data": {
                        "markdown": "x" * 250,
                        "metadata": {"title": "t"},
                    },
                }),
            )
            result = fetch_via_firecrawl("https://example.com", api_key="explicit")
            assert isinstance(result, ExtractedContent)
            sent = route.calls.last.request
            assert sent.headers["Authorization"] == "Bearer explicit"

    def test_env_var_fallback(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("FIRECRAWL_API_KEY", "from-env")
        with respx.mock:
            route = respx.post(FIRECRAWL_URL).mock(
                return_value=httpx.Response(200, json={
                    "data": {"markdown": "x" * 250, "metadata": {}},
                }),
            )
            fetch_via_firecrawl("https://example.com")
            assert route.calls.last.request.headers["Authorization"] == "Bearer from-env"


class TestFirecrawlSuccess:
    @respx.mock
    def test_success_returns_extracted_content(self) -> None:
        respx.post(FIRECRAWL_URL).mock(return_value=httpx.Response(200, json={
            "data": {
                "markdown": "# Hello\n\n" + ("body text " * 50),
                "metadata": {"title": "Page Title"},
            },
        }))
        result = fetch_via_firecrawl("https://x.com", api_key="k")
        assert isinstance(result, ExtractedContent)
        assert "Hello" in result.text
        assert result.title == "Page Title"
        assert result.status_code == 200

    @respx.mock
    def test_request_body_shape(self) -> None:
        route = respx.post(FIRECRAWL_URL).mock(return_value=httpx.Response(200, json={
            "data": {"markdown": "x" * 250, "metadata": {}},
        }))
        fetch_via_firecrawl("https://example.com/p", api_key="k")
        sent = _json.loads(route.calls.last.request.content)
        assert sent["url"] == "https://example.com/p"
        assert sent["formats"] == ["markdown"]

    @respx.mock
    def test_top_level_markdown_field_also_supported(self) -> None:
        # Some Firecrawl responses don't wrap in ``data``.
        respx.post(FIRECRAWL_URL).mock(return_value=httpx.Response(200, json={
            "markdown": "# Title\n\n" + ("content " * 50),
            "metadata": {"title": "Top"},
        }))
        result = fetch_via_firecrawl("https://x.com", api_key="k")
        assert "content" in result.text


class TestFirecrawlErrors:
    @respx.mock
    def test_429_raises_rate_limit_error(self) -> None:
        respx.post(FIRECRAWL_URL).mock(return_value=httpx.Response(429))
        with pytest.raises(FirecrawlRateLimitError):
            fetch_via_firecrawl("https://x.com", api_key="k")

    @respx.mock
    def test_401_raises_auth_error(self) -> None:
        respx.post(FIRECRAWL_URL).mock(return_value=httpx.Response(401))
        with pytest.raises(FirecrawlAuthError, match="FIRECRAWL_API_KEY"):
            fetch_via_firecrawl("https://x.com", api_key="bad")

    @respx.mock
    def test_403_raises_auth_error(self) -> None:
        respx.post(FIRECRAWL_URL).mock(return_value=httpx.Response(403))
        with pytest.raises(FirecrawlAuthError):
            fetch_via_firecrawl("https://x.com", api_key="bad")

    @respx.mock
    def test_500_raises_firecrawl_error(self) -> None:
        respx.post(FIRECRAWL_URL).mock(return_value=httpx.Response(500))
        with pytest.raises(FirecrawlError):
            fetch_via_firecrawl("https://x.com", api_key="k")

    @respx.mock
    def test_empty_markdown_raises_empty_result(self) -> None:
        respx.post(FIRECRAWL_URL).mock(return_value=httpx.Response(200, json={
            "data": {"markdown": "tiny", "metadata": {}},
        }))
        with pytest.raises(FirecrawlEmptyResultError):
            fetch_via_firecrawl("https://x.com", api_key="k")

    @respx.mock
    def test_missing_markdown_raises_empty_result(self) -> None:
        respx.post(FIRECRAWL_URL).mock(return_value=httpx.Response(200, json={
            "data": {"metadata": {}},
        }))
        with pytest.raises(FirecrawlEmptyResultError):
            fetch_via_firecrawl("https://x.com", api_key="k")

    @respx.mock
    def test_connection_error_raises_firecrawl_error(self) -> None:
        respx.post(FIRECRAWL_URL).mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(FirecrawlError, match="unreachable"):
            fetch_via_firecrawl("https://x.com", api_key="k")

    @respx.mock
    def test_invalid_json_raises_firecrawl_error(self) -> None:
        respx.post(FIRECRAWL_URL).mock(return_value=httpx.Response(200, text="not-json{"))
        with pytest.raises(FirecrawlError, match="parse"):
            fetch_via_firecrawl("https://x.com", api_key="k")


class TestFirecrawlInWebFetchAuto:
    """Integration: Firecrawl as third fallback in auto mode."""

    def test_no_firecrawl_api_key_path_silently_skipped(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from llm_code.tools.web_fetch import _try_firecrawl_extraction
        monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
        # cfg has default extraction_backend == "auto" → silently None.
        from unittest.mock import MagicMock
        cfg = MagicMock(
            extraction_backend="auto",
            firecrawl_api_key_env="FIRECRAWL_API_KEY",
        )
        assert _try_firecrawl_extraction("https://x.com", cfg) is None

    def test_explicit_firecrawl_no_key_raises(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from llm_code.tools.web_fetch import _try_firecrawl_extraction
        monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
        from unittest.mock import MagicMock
        cfg = MagicMock(
            extraction_backend="firecrawl",
            firecrawl_api_key_env="FIRECRAWL_API_KEY",
        )
        with pytest.raises(FirecrawlAuthError):
            _try_firecrawl_extraction("https://x.com", cfg)

    @respx.mock
    def test_explicit_firecrawl_mode_returns_markdown(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("FIRECRAWL_API_KEY", "k")
        respx.post(FIRECRAWL_URL).mock(return_value=httpx.Response(200, json={
            "data": {"markdown": "# X\n" + "a" * 250, "metadata": {"title": "X"}},
        }))
        from llm_code.tools.web_fetch import _try_firecrawl_extraction
        from unittest.mock import MagicMock
        cfg = MagicMock(
            extraction_backend="firecrawl",
            firecrawl_api_key_env="FIRECRAWL_API_KEY",
        )
        result = _try_firecrawl_extraction("https://x.com", cfg)
        assert result is not None
        body, ct, sc = result
        assert "a" in body
        assert ct == "text/markdown"
        assert sc == 200

    @respx.mock
    def test_auto_mode_429_falls_through_silently(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("FIRECRAWL_API_KEY", "k")
        respx.post(FIRECRAWL_URL).mock(return_value=httpx.Response(429))
        from llm_code.tools.web_fetch import _try_firecrawl_extraction
        from unittest.mock import MagicMock
        cfg = MagicMock(
            extraction_backend="auto",
            firecrawl_api_key_env="FIRECRAWL_API_KEY",
        )
        # auto mode swallows the rate limit and returns None.
        assert _try_firecrawl_extraction("https://x.com", cfg) is None
