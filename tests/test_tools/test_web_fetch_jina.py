"""Tests for Jina Reader fetch / extraction path (v2.7.0a1 M2).

Covers ``fetch_via_jina_reader`` (sync), ``fetch_via_jina_reader_async``
(async), and the WebFetchTool wiring that prefers Jina when
``WebFetchConfig.extraction_backend`` allows it.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx

from llm_code.tools.web_common import UrlCache
from llm_code.tools.web_fetch import (
    JinaReaderError,
    WebFetchTool,
    fetch_via_jina_reader,
    fetch_via_jina_reader_async,
)

JINA_BASE = "https://r.jina.ai/"

LONG_MARKDOWN = (
    "# Title\n\n"
    + "This is a long Jina-rendered article body. " * 30
)


# ---------------------------------------------------------------------------
# fetch_via_jina_reader (sync)
# ---------------------------------------------------------------------------


class TestFetchViaJinaReaderSync:
    @respx.mock
    def test_returns_markdown_body_on_200(self) -> None:
        respx.get(f"{JINA_BASE}https://example.com/article").mock(
            return_value=httpx.Response(200, text=LONG_MARKDOWN),
        )
        body, ct, status = fetch_via_jina_reader("https://example.com/article")
        assert body == LONG_MARKDOWN
        assert ct == "text/markdown"
        assert status == 200

    @respx.mock
    def test_sends_no_auth_header_when_anonymous(self) -> None:
        route = respx.get(f"{JINA_BASE}https://example.com/page").mock(
            return_value=httpx.Response(200, text=LONG_MARKDOWN),
        )
        fetch_via_jina_reader("https://example.com/page")
        sent = route.calls.last.request
        assert "Authorization" not in sent.headers
        assert sent.headers["Accept"] == "text/markdown"

    @respx.mock
    def test_sends_bearer_auth_when_key_set(self) -> None:
        route = respx.get(f"{JINA_BASE}https://example.com/auth").mock(
            return_value=httpx.Response(200, text=LONG_MARKDOWN),
        )
        fetch_via_jina_reader("https://example.com/auth", api_key="jina-secret")
        sent = route.calls.last.request
        assert sent.headers["Authorization"] == "Bearer jina-secret"

    @respx.mock
    def test_429_raises_jina_reader_error(self) -> None:
        respx.get(f"{JINA_BASE}https://example.com/rl").mock(
            return_value=httpx.Response(429),
        )
        with pytest.raises(JinaReaderError, match="rate limited"):
            fetch_via_jina_reader("https://example.com/rl")

    @respx.mock
    def test_500_raises_jina_reader_error(self) -> None:
        respx.get(f"{JINA_BASE}https://example.com/err").mock(
            return_value=httpx.Response(500),
        )
        with pytest.raises(JinaReaderError, match="HTTP 500"):
            fetch_via_jina_reader("https://example.com/err")

    @respx.mock
    def test_connection_error_raises_jina_reader_error(self) -> None:
        respx.get(f"{JINA_BASE}https://example.com/conn").mock(
            side_effect=httpx.ConnectError("refused"),
        )
        with pytest.raises(JinaReaderError, match="unreachable"):
            fetch_via_jina_reader("https://example.com/conn")

    @respx.mock
    def test_empty_body_raises_jina_reader_error(self) -> None:
        respx.get(f"{JINA_BASE}https://example.com/empty").mock(
            return_value=httpx.Response(200, text=""),
        )
        with pytest.raises(JinaReaderError, match="empty"):
            fetch_via_jina_reader("https://example.com/empty")

    @respx.mock
    def test_short_body_raises_jina_reader_error(self) -> None:
        respx.get(f"{JINA_BASE}https://example.com/short").mock(
            return_value=httpx.Response(200, text="too short"),
        )
        with pytest.raises(JinaReaderError, match="empty"):
            fetch_via_jina_reader("https://example.com/short")


# ---------------------------------------------------------------------------
# fetch_via_jina_reader_async
# ---------------------------------------------------------------------------


class TestFetchViaJinaReaderAsync:
    @respx.mock
    async def test_returns_markdown_body_on_200(self) -> None:
        respx.get(f"{JINA_BASE}https://example.com/async-article").mock(
            return_value=httpx.Response(200, text=LONG_MARKDOWN),
        )
        body, ct, status = await fetch_via_jina_reader_async(
            "https://example.com/async-article"
        )
        assert body == LONG_MARKDOWN
        assert ct == "text/markdown"
        assert status == 200

    @respx.mock
    async def test_429_raises_jina_reader_error(self) -> None:
        respx.get(f"{JINA_BASE}https://example.com/async-rl").mock(
            return_value=httpx.Response(429),
        )
        with pytest.raises(JinaReaderError):
            await fetch_via_jina_reader_async("https://example.com/async-rl")


# ---------------------------------------------------------------------------
# WebFetchTool integration
# ---------------------------------------------------------------------------


class TestWebFetchJinaIntegration:
    """End-to-end: WebFetchTool prefers Jina when extraction_backend allows."""

    def test_jina_success_short_circuits_local_fetch(self) -> None:
        """When Jina returns a long body, the local httpx path is skipped."""
        tool = WebFetchTool(cache=UrlCache())  # fresh cache

        # Mock httpx.get to discriminate Jina vs original-URL calls.
        def mock_get(url, **kwargs):  # noqa: ARG001
            if "r.jina.ai" in url:
                resp = MagicMock()
                resp.status_code = 200
                resp.text = LONG_MARKDOWN
                resp.headers = {"content-type": "text/markdown"}
                return resp
            # Original URL — should NOT be reached when Jina succeeds.
            raise AssertionError(f"local httpx.get should not be called for {url}")

        with patch("httpx.get", side_effect=mock_get):
            result = tool.execute({"url": "https://example.com/jina-success"})

        assert result.is_error is False
        assert result.metadata["extraction_backend"] == "jina"
        assert "Jina-rendered article" in result.output

    def test_jina_failure_falls_back_to_local(self) -> None:
        """When Jina rate-limits, web_fetch silently falls back to local."""
        tool = WebFetchTool(cache=UrlCache())

        def mock_get(url, **kwargs):  # noqa: ARG001
            if "r.jina.ai" in url:
                resp = MagicMock()
                resp.status_code = 429
                resp.text = ""
                resp.headers = {}
                return resp
            # Local httpx success.
            resp = MagicMock()
            resp.status_code = 200
            resp.text = "<html><body><p>Local content</p></body></html>"
            resp.headers = {"content-type": "text/html"}
            return resp

        with patch("httpx.get", side_effect=mock_get):
            result = tool.execute({"url": "https://example.com/jina-rl"})

        assert result.is_error is False
        # Falls back to local extraction — backend marker reflects that.
        assert result.metadata["extraction_backend"] == "local"
        assert len(result.output) > 0

    def test_raw_mode_skips_jina(self) -> None:
        """``raw=True`` callers want the unprocessed page — bypass Jina."""
        tool = WebFetchTool(cache=UrlCache())

        def mock_get(url, **kwargs):  # noqa: ARG001
            assert "r.jina.ai" not in url, "raw=True should not hit Jina"
            resp = MagicMock()
            resp.status_code = 200
            resp.text = "<html><body><p>raw content</p></body></html>"
            resp.headers = {"content-type": "text/html"}
            return resp

        with patch("httpx.get", side_effect=mock_get):
            result = tool.execute(
                {"url": "https://example.com/raw", "raw": True},
            )
        assert result.is_error is False
        # raw=True path uses local extraction; no Jina consultation.
        assert result.metadata["extraction_backend"] == "local"

    def test_extraction_backend_local_disables_jina(self) -> None:
        """``extraction_backend == "local"`` skips Jina entirely."""
        tool = WebFetchTool(cache=UrlCache())

        def mock_get(url, **kwargs):  # noqa: ARG001
            assert "r.jina.ai" not in url, "local-only mode should not hit Jina"
            resp = MagicMock()
            resp.status_code = 200
            resp.text = "<html><body>local-only path</body></html>"
            resp.headers = {"content-type": "text/html"}
            return resp

        # Force config to "local" by patching the loader.
        from llm_code.runtime.config import WebFetchConfig
        local_cfg = WebFetchConfig(extraction_backend="local")
        with patch.object(WebFetchTool, "_get_web_fetch_config", return_value=local_cfg):
            with patch("httpx.get", side_effect=mock_get):
                result = tool.execute({"url": "https://example.com/local-only"})

        assert result.is_error is False
        assert result.metadata["extraction_backend"] == "local"

    def test_extraction_backend_jina_strict_propagates_error(self) -> None:
        """``extraction_backend == "jina"`` returns an error when Jina fails."""
        tool = WebFetchTool(cache=UrlCache())

        def mock_get(url, **kwargs):  # noqa: ARG001
            if "r.jina.ai" in url:
                resp = MagicMock()
                resp.status_code = 503
                resp.text = ""
                resp.headers = {}
                return resp
            raise AssertionError("Jina-only mode should not fall back to local")

        from llm_code.runtime.config import WebFetchConfig
        strict_cfg = WebFetchConfig(extraction_backend="jina")
        with patch.object(WebFetchTool, "_get_web_fetch_config", return_value=strict_cfg):
            with patch("httpx.get", side_effect=mock_get):
                result = tool.execute({"url": "https://example.com/jina-only"})

        assert result.is_error is True
        assert "Jina Reader extraction failed" in result.output

    def test_extraction_backend_unknown_falls_back_to_auto(self) -> None:
        """Unknown ``extraction_backend`` values default to "auto" mode."""
        tool = WebFetchTool(cache=UrlCache())

        def mock_get(url, **kwargs):  # noqa: ARG001
            if "r.jina.ai" in url:
                resp = MagicMock()
                resp.status_code = 200
                resp.text = LONG_MARKDOWN
                resp.headers = {"content-type": "text/markdown"}
                return resp
            raise AssertionError("Jina succeeded — local should not be called")

        # Use an instance with an invalid extraction_backend by manual
        # construction (skirts the dataclass to test defensive fallback).
        from llm_code.runtime.config import WebFetchConfig
        cfg = WebFetchConfig(extraction_backend="auto")
        # Mutate to an unknown value using object.__setattr__ on frozen dc.
        object.__setattr__(cfg, "extraction_backend", "made-up-mode")
        with patch.object(WebFetchTool, "_get_web_fetch_config", return_value=cfg):
            with patch("httpx.get", side_effect=mock_get):
                result = tool.execute(
                    {"url": "https://example.com/unknown-backend"},
                )
        assert result.is_error is False
        assert result.metadata["extraction_backend"] == "jina"
