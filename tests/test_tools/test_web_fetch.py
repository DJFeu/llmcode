"""Tests for WebFetchTool."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


from llm_code.tools.base import PermissionLevel, ToolResult
from llm_code.tools.web_fetch import WebFetchTool


class TestWebFetchToolProperties:
    def test_name(self) -> None:
        tool = WebFetchTool()
        assert tool.name == "web_fetch"

    def test_required_permission(self) -> None:
        tool = WebFetchTool()
        assert tool.required_permission == PermissionLevel.READ_ONLY

    def test_is_read_only_true(self) -> None:
        # Network read — does not mutate local FS.
        tool = WebFetchTool()
        assert tool.is_read_only({}) is True

    def test_is_concurrency_safe_true(self) -> None:
        tool = WebFetchTool()
        assert tool.is_concurrency_safe({}) is True

    def test_description_non_empty(self) -> None:
        tool = WebFetchTool()
        assert len(tool.description) > 0

    def test_input_schema_is_dict(self) -> None:
        tool = WebFetchTool()
        schema = tool.input_schema
        assert isinstance(schema, dict)
        assert schema.get("type") == "object"

    def test_input_schema_has_url_required(self) -> None:
        tool = WebFetchTool()
        schema = tool.input_schema
        assert "url" in schema.get("properties", {})
        assert "url" in schema.get("required", [])

    def test_input_schema_has_optional_fields(self) -> None:
        tool = WebFetchTool()
        props = tool.input_schema.get("properties", {})
        assert "prompt" in props
        assert "max_length" in props
        assert "raw" in props
        assert "renderer" in props

    def test_input_schema_renderer_enum(self) -> None:
        tool = WebFetchTool()
        renderer_prop = tool.input_schema["properties"]["renderer"]
        assert "enum" in renderer_prop
        assert set(renderer_prop["enum"]) == {"auto", "default", "browser"}

    def test_input_model_returns_pydantic_model(self) -> None:
        from pydantic import BaseModel
        tool = WebFetchTool()
        model_cls = tool.input_model
        assert model_cls is not None
        assert issubclass(model_cls, BaseModel)


class TestWebFetchToolBlockedURL:
    def test_blocked_url_returns_error(self) -> None:
        tool = WebFetchTool()
        result = tool.execute({"url": "file:///etc/passwd"})
        assert isinstance(result, ToolResult)
        assert result.is_error is True
        assert "blocked" in result.output.lower()

    def test_private_ip_returns_error(self) -> None:
        tool = WebFetchTool()
        result = tool.execute({"url": "http://192.168.1.1/admin"})
        assert isinstance(result, ToolResult)
        assert result.is_error is True

    def test_metadata_schema_url_returns_error(self) -> None:
        tool = WebFetchTool()
        result = tool.execute({"url": "http://169.254.169.254/latest/meta-data"})
        assert result.is_error is True


class TestWebFetchToolMissingURL:
    def test_missing_url_returns_error(self) -> None:
        tool = WebFetchTool()
        result = tool.execute({})
        assert isinstance(result, ToolResult)
        assert result.is_error is True

    def test_empty_url_returns_error(self) -> None:
        tool = WebFetchTool()
        result = tool.execute({"url": ""})
        assert isinstance(result, ToolResult)
        assert result.is_error is True


class TestWebFetchToolSuccessfulFetch:
    def test_successful_html_fetch(self) -> None:
        tool = WebFetchTool()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body><h1>Hello</h1></body></html>"
        mock_response.headers = {"content-type": "text/html; charset=utf-8"}

        with patch("httpx.get", return_value=mock_response) as mock_get:
            result = tool.execute({"url": "https://example.com"})

        assert isinstance(result, ToolResult)
        assert result.is_error is False
        assert len(result.output) > 0
        # v2.7.0a1 M2: Jina is tried first (anonymous tier) and falls back
        # to local httpx because the short mock body fails the
        # _MIN_USEFUL_CHARS threshold. Either both URLs were hit (2 calls)
        # or just the local one (cfg unavailable, 1 call). Assert that
        # at least the original URL was reached.
        assert mock_get.called
        called_urls = [c.args[0] for c in mock_get.call_args_list if c.args]
        assert any("example.com" in u for u in called_urls)

    def test_successful_json_fetch(self) -> None:
        tool = WebFetchTool()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"key": "value", "number": 42}'
        mock_response.headers = {"content-type": "application/json"}

        with patch("httpx.get", return_value=mock_response):
            result = tool.execute({"url": "https://api.example.com/data"})

        assert result.is_error is False
        assert '"key"' in result.output
        assert '"value"' in result.output

    def test_result_metadata_contains_url(self) -> None:
        tool = WebFetchTool()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body>content</body></html>"
        mock_response.headers = {"content-type": "text/html"}

        with patch("httpx.get", return_value=mock_response):
            result = tool.execute({"url": "https://example.com/page"})

        assert result.metadata is not None
        assert result.metadata.get("url") == "https://example.com/page"

    def test_result_metadata_contains_status_code(self) -> None:
        from llm_code.tools.web_common import UrlCache
        tool = WebFetchTool(cache=UrlCache())  # fresh cache to avoid cross-test pollution
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "plain text"
        mock_response.headers = {"content-type": "text/plain"}

        with patch("httpx.get", return_value=mock_response):
            result = tool.execute({"url": "https://example.com/status-test"})

        assert result.metadata is not None
        assert result.metadata.get("status_code") == 200

    def test_result_metadata_cached_false_on_first_fetch(self) -> None:
        tool = WebFetchTool()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "content"
        mock_response.headers = {"content-type": "text/plain"}

        with patch("httpx.get", return_value=mock_response):
            result = tool.execute({"url": "https://example.com/fresh"})

        assert result.metadata is not None
        assert result.metadata.get("cached") is False

    def test_max_length_truncation(self) -> None:
        tool = WebFetchTool()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "A" * 1000
        mock_response.headers = {"content-type": "text/plain"}

        with patch("httpx.get", return_value=mock_response):
            result = tool.execute({"url": "https://example.com", "max_length": 100})

        assert result.is_error is False
        assert len(result.output) <= 120  # some overhead for truncation marker


class TestWebFetchToolHTTPError:
    def test_404_returns_error(self) -> None:
        from llm_code.tools.web_common import UrlCache
        tool = WebFetchTool(cache=UrlCache())
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"
        mock_response.headers = {"content-type": "text/plain"}
        mock_response.raise_for_status.side_effect = Exception("404 Not Found")

        with patch("httpx.get", return_value=mock_response):
            result = tool.execute({"url": "https://example.com/missing"})

        assert result.is_error is True

    def test_network_error_returns_error(self) -> None:
        from llm_code.tools.web_common import UrlCache
        tool = WebFetchTool(cache=UrlCache())
        import httpx
        with patch("httpx.get", side_effect=httpx.RequestError("Connection refused", request=MagicMock())):
            result = tool.execute({"url": "https://example.com/network-error"})

        assert result.is_error is True
        assert len(result.output) > 0


class TestWebFetchToolCacheHit:
    def test_cache_hit_reuses_content(self) -> None:
        tool = WebFetchTool()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body>cached content</body></html>"
        mock_response.headers = {"content-type": "text/html"}

        url = "https://example.com/cached-page"

        with patch("httpx.get", return_value=mock_response) as mock_get:
            # First fetch — should call httpx (possibly twice: Jina then local)
            result1 = tool.execute({"url": url})
            first_call_count = mock_get.call_count
            # Second fetch — should use cache (NO additional httpx call)
            result2 = tool.execute({"url": url})

        # The second fetch must not trigger any additional HTTP traffic.
        assert mock_get.call_count == first_call_count
        assert result1.output == result2.output

    def test_cache_hit_metadata_cached_true(self) -> None:
        tool = WebFetchTool()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "some content"
        mock_response.headers = {"content-type": "text/plain"}

        url = "https://example.com/cached-meta"

        with patch("httpx.get", return_value=mock_response):
            tool.execute({"url": url})
            result = tool.execute({"url": url})

        assert result.metadata is not None
        assert result.metadata.get("cached") is True


class TestWebFetchToolRendererResolution:
    def test_renderer_auto_resolves_to_default(self) -> None:
        """renderer=auto without playwright falls back to default httpx."""
        from llm_code.tools.web_common import UrlCache
        tool = WebFetchTool(cache=UrlCache())
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "content"
        mock_response.headers = {"content-type": "text/plain"}

        with patch("httpx.get", return_value=mock_response) as mock_get:
            with patch.dict("sys.modules", {"playwright": None}):
                result = tool.execute({"url": "https://example.com/renderer-auto", "renderer": "auto"})

        assert result.is_error is False
        # Jina-first then local fallback when short body fails threshold;
        # at minimum the original URL was hit.
        assert mock_get.called
        called_urls = [c.args[0] for c in mock_get.call_args_list if c.args]
        assert any("renderer-auto" in u for u in called_urls)

    def test_renderer_default_uses_httpx(self) -> None:
        from llm_code.tools.web_common import UrlCache
        tool = WebFetchTool(cache=UrlCache())
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "content"
        mock_response.headers = {"content-type": "text/plain"}

        with patch("httpx.get", return_value=mock_response) as mock_get:
            result = tool.execute({"url": "https://example.com/renderer-default", "renderer": "default"})

        assert result.is_error is False
        # Jina-first then local fallback when short body fails threshold;
        # at minimum the original URL was hit.
        assert mock_get.called
        called_urls = [c.args[0] for c in mock_get.call_args_list if c.args]
        assert any("renderer-default" in u for u in called_urls)
