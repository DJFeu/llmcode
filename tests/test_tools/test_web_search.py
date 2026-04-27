"""Tests for WebSearchTool."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from llm_code.tools.base import PermissionLevel, ToolResult
from llm_code.tools.search_backends import SearchResult
from llm_code.tools.web_search import WebSearchTool, _augment_time_sensitive_query


class TestWebSearchToolProperties:
    """Test WebSearchTool properties."""

    def setup_method(self) -> None:
        self.tool = WebSearchTool()

    def test_name(self) -> None:
        assert self.tool.name == "web_search"

    def test_required_permission(self) -> None:
        assert self.tool.required_permission == PermissionLevel.READ_ONLY

    def test_is_read_only(self) -> None:
        # Network read — does not mutate local FS, so qualifies as read-only
        # for the purposes of speculative concurrent dispatch.
        assert self.tool.is_read_only({}) is True

    def test_is_concurrency_safe(self) -> None:
        assert self.tool.is_concurrency_safe({}) is True

    def test_input_schema_has_query(self) -> None:
        schema = self.tool.input_schema
        assert "query" in schema["properties"]
        assert "query" in schema["required"]

    def test_input_schema_has_max_results(self) -> None:
        schema = self.tool.input_schema
        assert "max_results" in schema["properties"]

    def test_input_schema_has_backend(self) -> None:
        schema = self.tool.input_schema
        assert "backend" in schema["properties"]

    def test_description_is_non_empty(self) -> None:
        assert len(self.tool.description) > 0


class TestDomainFiltering:
    """Test domain filtering logic."""

    def setup_method(self) -> None:
        self.tool = WebSearchTool()
        self.results = (
            SearchResult(title="Example", url="https://example.com/page", snippet="test"),
            SearchResult(title="Blocked", url="https://blocked.com/page", snippet="bad"),
            SearchResult(title="Another", url="https://another.org/page", snippet="ok"),
            SearchResult(title="Sub", url="https://sub.example.com/page", snippet="sub"),
        )

    def test_denylist_filters_matching_domains(self) -> None:
        filtered = self.tool._filter_results(
            self.results,
            domain_allowlist=(),
            domain_denylist=("blocked.com",),
        )
        urls = [r.url for r in filtered]
        assert not any("blocked.com" in u for u in urls)

    def test_denylist_allows_non_matching(self) -> None:
        filtered = self.tool._filter_results(
            self.results,
            domain_allowlist=(),
            domain_denylist=("blocked.com",),
        )
        assert len(filtered) == len(self.results) - 1

    def test_allowlist_filters_non_matching_domains(self) -> None:
        filtered = self.tool._filter_results(
            self.results,
            domain_allowlist=("example.com",),
            domain_denylist=(),
        )
        urls = [r.url for r in filtered]
        assert all("example.com" in u for u in urls)

    def test_allowlist_with_wildcard(self) -> None:
        filtered = self.tool._filter_results(
            self.results,
            domain_allowlist=("*.org",),
            domain_denylist=(),
        )
        urls = [r.url for r in filtered]
        assert all(".org" in u for u in urls)

    def test_empty_lists_allow_all(self) -> None:
        filtered = self.tool._filter_results(
            self.results,
            domain_allowlist=(),
            domain_denylist=(),
        )
        assert len(filtered) == len(self.results)

    def test_denylist_applied_before_allowlist(self) -> None:
        # A domain in both denylist and allowlist should be denied
        filtered = self.tool._filter_results(
            self.results,
            domain_allowlist=("example.com", "blocked.com"),
            domain_denylist=("blocked.com",),
        )
        urls = [r.url for r in filtered]
        assert not any("blocked.com" in u for u in urls)

    def test_denylist_fnmatch_wildcard(self) -> None:
        filtered = self.tool._filter_results(
            self.results,
            domain_allowlist=(),
            domain_denylist=("*.com",),
        )
        urls = [r.url for r in filtered]
        assert not any(".com" in u for u in urls)


class TestFormatResults:
    """Test result formatting."""

    def setup_method(self) -> None:
        self.tool = WebSearchTool()

    def test_format_empty_results(self) -> None:
        output = self.tool._format_results("test query", ())
        assert "test query" in output
        assert "0 results" in output

    def test_format_single_result(self) -> None:
        results = (
            SearchResult(title="Example Title", url="https://example.com", snippet="A snippet."),
        )
        output = self.tool._format_results("test query", results)
        assert "Example Title" in output
        assert "https://example.com" in output
        assert "A snippet." in output
        assert "1." in output

    def test_format_multiple_results(self) -> None:
        results = (
            SearchResult(title="First", url="https://first.com", snippet="Snippet 1"),
            SearchResult(title="Second", url="https://second.com", snippet="Snippet 2"),
        )
        output = self.tool._format_results("my query", results)
        assert "1." in output
        assert "2." in output
        assert "First" in output
        assert "Second" in output

    def test_format_header_contains_query(self) -> None:
        results = (
            SearchResult(title="T", url="https://x.com", snippet="S"),
        )
        output = self.tool._format_results("my search query", results)
        assert "my search query" in output

    def test_format_result_count(self) -> None:
        results = (
            SearchResult(title="T1", url="https://a.com", snippet="S1"),
            SearchResult(title="T2", url="https://b.com", snippet="S2"),
            SearchResult(title="T3", url="https://c.com", snippet="S3"),
        )
        output = self.tool._format_results("query", results)
        assert "3" in output


class TestWebSearchToolExecute:
    """Test execute method."""

    def setup_method(self) -> None:
        self.tool = WebSearchTool()

    def test_missing_query_returns_error(self) -> None:
        result = self.tool.execute({})
        assert result.is_error is True
        assert "query" in result.output.lower()

    def test_empty_query_returns_error(self) -> None:
        result = self.tool.execute({"query": ""})
        assert result.is_error is True

    def test_execute_success(self) -> None:
        mock_results = (
            SearchResult(title="Test Result", url="https://example.com", snippet="A snippet"),
        )
        mock_backend = MagicMock()
        mock_backend.search.return_value = mock_results

        with patch("llm_code.tools.web_search.create_backend", return_value=mock_backend):
            result = self.tool.execute({"query": "test search"})

        assert result.is_error is False
        assert "Test Result" in result.output

    def test_execute_returns_tool_result(self) -> None:
        mock_backend = MagicMock()
        mock_backend.search.return_value = ()

        with patch("llm_code.tools.web_search.create_backend", return_value=mock_backend):
            result = self.tool.execute({"query": "test"})

        assert isinstance(result, ToolResult)

    def test_execute_with_max_results(self) -> None:
        mock_backend = MagicMock()
        mock_backend.search.return_value = ()

        # auto-fallback chain may try multiple backends — assert that
        # every search() call carried the requested max_results.
        with patch("llm_code.tools.web_search.create_backend", return_value=mock_backend):
            self.tool.execute({"query": "test", "max_results": 5})

        assert mock_backend.search.called
        for call in mock_backend.search.call_args_list:
            assert call.kwargs.get("max_results") == 5
            assert call.args == ("test",)

    def test_execute_default_max_results(self) -> None:
        mock_backend = MagicMock()
        mock_backend.search.return_value = ()

        with patch("llm_code.tools.web_search.create_backend", return_value=mock_backend):
            self.tool.execute({"query": "test"})

        assert mock_backend.search.called
        for call in mock_backend.search.call_args_list:
            assert call.kwargs.get("max_results") == 10
            assert call.args == ("test",)

    def test_execute_backend_exception_returns_error(self) -> None:
        # When a specific (non-auto) backend raises, the tool returns an error.
        mock_backend = MagicMock()
        mock_backend.search.side_effect = Exception("network error")

        with patch("llm_code.tools.web_search.create_backend", return_value=mock_backend):
            result = self.tool.execute({"query": "test", "backend": "duckduckgo"})

        assert result.is_error is True

    def test_execute_with_explicit_duckduckgo_backend(self) -> None:
        mock_backend = MagicMock()
        mock_backend.search.return_value = ()

        with patch("llm_code.tools.web_search.create_backend", return_value=mock_backend) as mock_create:
            self.tool.execute({"query": "test", "backend": "duckduckgo"})

        mock_create.assert_called_once_with("duckduckgo")


# ---------------------------------------------------------------------------
# DuckDuckGo rate limit and fallback
# ---------------------------------------------------------------------------


class TestDuckDuckGoRateLimit:
    """Test DDG rate limit detection and fallback chain behavior."""

    def test_http_429_raises_rate_limit_error(self) -> None:
        from llm_code.tools.search_backends import RateLimitError
        from llm_code.tools.search_backends.duckduckgo import DuckDuckGoBackend

        backend = DuckDuckGoBackend()
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = ""

        with patch("httpx.get", return_value=mock_response):
            with pytest.raises(RateLimitError):
                backend.search("test query")

    def test_bot_detection_raises_rate_limit_error(self) -> None:
        from llm_code.tools.search_backends import RateLimitError
        from llm_code.tools.search_backends.duckduckgo import DuckDuckGoBackend

        backend = DuckDuckGoBackend()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body>If you are not a bot, please try again.</body></html>"

        with patch("httpx.get", return_value=mock_response):
            with pytest.raises(RateLimitError):
                backend.search("test query")

    def test_normal_empty_results_no_error(self) -> None:
        from llm_code.tools.search_backends.duckduckgo import DuckDuckGoBackend

        backend = DuckDuckGoBackend()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body>No results found</body></html>"

        with patch("httpx.get", return_value=mock_response):
            results = backend.search("very obscure query")
            assert results == ()

    def test_fallback_continues_after_rate_limit(self) -> None:
        from llm_code.tools.search_backends import RateLimitError

        tool = WebSearchTool()
        call_count = 0
        fallback_results = (
            SearchResult(title="Fallback", url="https://fallback.com", snippet="Got it"),
        )

        def mock_create(name, **kwargs):
            nonlocal call_count
            call_count += 1
            mock = MagicMock()
            if name == "duckduckgo":
                mock.search.side_effect = RateLimitError("rate limited")
            else:
                mock.search.return_value = fallback_results
            return mock

        with patch("llm_code.tools.web_search.create_backend", side_effect=mock_create):
            with patch.dict("os.environ", {"BRAVE_API_KEY": "test-key"}, clear=False):
                # Ensure no other paid-backend env vars leak in from the host shell.
                import os as _os
                for _k in (
                    "EXA_API_KEY",
                    "JINA_API_KEY",
                    "LINKUP_API_KEY",
                    "TAVILY_API_KEY",
                    "SERPER_API_KEY",
                ):
                    _os.environ.pop(_k, None)
                results = tool._search_with_fallback("test", 10, MagicMock(
                    brave_api_key_env="BRAVE_API_KEY",
                    exa_api_key_env="EXA_API_KEY",
                    jina_api_key_env="JINA_API_KEY",
                    linkup_api_key_env="LINKUP_API_KEY",
                    searxng_base_url="",
                    serper_api_key_env="SERPER_API_KEY",
                    tavily_api_key_env="TAVILY_API_KEY",
                ))
        assert len(results) == 1
        assert results[0].title == "Fallback"
        assert call_count >= 2


class TestAugmentTimeSensitiveQuery:
    """Tool-side query hygiene: append today's ISO date when a query
    signals current-moment intent but omits an explicit date."""

    def test_today_trigger_appends_date(self) -> None:
        out = _augment_time_sensitive_query("today's top news")
        assert out.startswith("today's top news ")
        import re
        assert re.search(r"\b\d{4}-\d{2}-\d{2}$", out)

    def test_jp_trigger_appends_date(self) -> None:
        out = _augment_time_sensitive_query("今日熱門新聞")
        import re
        assert re.search(r"\b\d{4}-\d{2}-\d{2}$", out)

    def test_existing_iso_date_preserved(self) -> None:
        q = "today news 2026-04-01"
        assert _augment_time_sensitive_query(q) == q

    def test_no_trigger_unchanged(self) -> None:
        q = "python asyncio tutorial"
        assert _augment_time_sensitive_query(q) == q

    def test_case_insensitive(self) -> None:
        out = _augment_time_sensitive_query("Latest AI news")
        import re
        assert re.search(r"\b\d{4}-\d{2}-\d{2}$", out)

    def test_month_only_still_augmented(self) -> None:
        """Month-level query with a today-trigger still gets a precise
        date appended — month alone hits stale archive pages."""
        out = _augment_time_sensitive_query("今日熱門新聞 2026年4月")
        import re
        assert re.search(r"\b\d{4}-\d{2}-\d{2}$", out)
