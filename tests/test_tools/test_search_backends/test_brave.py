"""Tests for Brave Search backend."""
from __future__ import annotations

import httpx
import pytest
import respx

from llm_code.tools.search_backends.brave import BraveBackend

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"


class TestBraveBackend:
    def test_empty_api_key_raises(self) -> None:
        with pytest.raises(ValueError):
            BraveBackend(api_key="")

    @respx.mock
    def test_search_success(self) -> None:
        respx.get(BRAVE_URL).mock(return_value=httpx.Response(200, json={
            "web": {"results": [
                {"title": "Test", "url": "https://example.com", "description": "A test result"},
            ]}
        }))
        backend = BraveBackend(api_key="test-key")
        results = backend.search("test query")
        assert len(results) == 1
        assert results[0].title == "Test"
        assert results[0].url == "https://example.com"
        assert results[0].snippet == "A test result"

    @respx.mock
    def test_search_empty(self) -> None:
        respx.get(BRAVE_URL).mock(return_value=httpx.Response(200, json={"web": {"results": []}}))
        backend = BraveBackend(api_key="test-key")
        results = backend.search("no results query")
        assert results == ()

    @respx.mock
    def test_search_connection_error(self) -> None:
        respx.get(BRAVE_URL).mock(side_effect=httpx.ConnectError("refused"))
        backend = BraveBackend(api_key="test-key")
        results = backend.search("test")
        assert results == ()

    @respx.mock
    def test_search_server_error(self) -> None:
        respx.get(BRAVE_URL).mock(return_value=httpx.Response(500))
        backend = BraveBackend(api_key="test-key")
        results = backend.search("test")
        assert results == ()
