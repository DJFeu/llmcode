"""Tests for Linkup ``sourcedAnswer`` mode (v2.8.0 M3)."""
from __future__ import annotations

import json as _json

import httpx
import pytest
import respx

from llm_code.tools.search_backends import RateLimitError
from llm_code.tools.search_backends.linkup import (
    LinkupBackend,
    Source,
    SourcedAnswer,
)

LINKUP_URL = "https://api.linkup.so/v1/search"


class TestSourcedAnswerDataclasses:
    def test_source_is_frozen_dataclass(self) -> None:
        s = Source(title="t", url="https://x.com", snippet="snip")
        with pytest.raises(Exception):
            s.title = "changed"  # type: ignore[misc]

    def test_sourced_answer_is_frozen_dataclass(self) -> None:
        s = Source(title="t", url="https://x.com", snippet="snip")
        a = SourcedAnswer(answer="hello", sources=(s,))
        with pytest.raises(Exception):
            a.answer = "changed"  # type: ignore[misc]

    def test_sources_immutable_tuple(self) -> None:
        a = SourcedAnswer(answer="hi", sources=())
        assert isinstance(a.sources, tuple)


class TestLinkupSourcedAnswer:
    @respx.mock
    def test_sourced_answer_success(self) -> None:
        respx.post(LINKUP_URL).mock(return_value=httpx.Response(200, json={
            "answer": "Transformers use self-attention to process sequences.",
            "sources": [
                {
                    "name": "Attention Is All You Need",
                    "url": "https://arxiv.org/abs/1706.03762",
                    "snippet": "Original transformer paper.",
                },
                {
                    "name": "The Illustrated Transformer",
                    "url": "https://jalammar.github.io/illustrated-transformer/",
                    "snippet": "Visual explanation.",
                },
            ],
        }))
        backend = LinkupBackend(api_key="test-key")
        result = backend.sourced_answer("explain transformers")
        assert isinstance(result, SourcedAnswer)
        assert "self-attention" in result.answer
        assert len(result.sources) == 2
        assert result.sources[0].title == "Attention Is All You Need"
        assert result.sources[0].url.startswith("https://arxiv.org")

    @respx.mock
    def test_sourced_answer_request_body_shape(self) -> None:
        route = respx.post(LINKUP_URL).mock(return_value=httpx.Response(200, json={
            "answer": "ans", "sources": [],
        }))
        backend = LinkupBackend(api_key="my-secret")
        backend.sourced_answer("the query")
        sent = _json.loads(route.calls.last.request.content)
        assert sent["q"] == "the query"
        assert sent["outputType"] == "sourcedAnswer"
        assert sent["depth"] == "standard"
        assert sent["includeImages"] is False

    @respx.mock
    def test_sourced_answer_deep_depth(self) -> None:
        route = respx.post(LINKUP_URL).mock(return_value=httpx.Response(200, json={
            "answer": "ans", "sources": [],
        }))
        backend = LinkupBackend(api_key="test-key")
        backend.sourced_answer("q", depth="deep")
        sent = _json.loads(route.calls.last.request.content)
        assert sent["depth"] == "deep"

    @respx.mock
    def test_sourced_answer_429_raises_rate_limit(self) -> None:
        respx.post(LINKUP_URL).mock(return_value=httpx.Response(429))
        backend = LinkupBackend(api_key="test-key")
        with pytest.raises(RateLimitError):
            backend.sourced_answer("q")

    @respx.mock
    def test_sourced_answer_401_raises_value_error_with_env_var(self) -> None:
        respx.post(LINKUP_URL).mock(return_value=httpx.Response(401))
        backend = LinkupBackend(api_key="bad-key")
        with pytest.raises(ValueError, match="LINKUP_API_KEY"):
            backend.sourced_answer("q")

    @respx.mock
    def test_sourced_answer_403_raises_value_error(self) -> None:
        respx.post(LINKUP_URL).mock(return_value=httpx.Response(403))
        backend = LinkupBackend(api_key="bad-key")
        with pytest.raises(ValueError, match="LINKUP_API_KEY"):
            backend.sourced_answer("q")

    @respx.mock
    def test_sourced_answer_500_raises_value_error(self) -> None:
        respx.post(LINKUP_URL).mock(return_value=httpx.Response(500))
        backend = LinkupBackend(api_key="test-key")
        with pytest.raises(ValueError, match="HTTP 500"):
            backend.sourced_answer("q")

    @respx.mock
    def test_sourced_answer_empty_sources_returns_empty_tuple_not_none(self) -> None:
        respx.post(LINKUP_URL).mock(return_value=httpx.Response(200, json={
            "answer": "no citations available",
            "sources": [],
        }))
        backend = LinkupBackend(api_key="test-key")
        result = backend.sourced_answer("q")
        assert result.sources == ()
        assert result.sources is not None

    @respx.mock
    def test_sourced_answer_missing_sources_field_returns_empty_tuple(self) -> None:
        # Defensive: future API tweak that omits ``sources`` entirely.
        respx.post(LINKUP_URL).mock(return_value=httpx.Response(200, json={
            "answer": "just an answer",
        }))
        backend = LinkupBackend(api_key="test-key")
        result = backend.sourced_answer("q")
        assert result.answer == "just an answer"
        assert result.sources == ()

    @respx.mock
    def test_sourced_answer_skips_sources_without_url(self) -> None:
        respx.post(LINKUP_URL).mock(return_value=httpx.Response(200, json={
            "answer": "ans",
            "sources": [
                {"name": "ok", "url": "https://ok.com", "snippet": "s"},
                {"name": "no url", "snippet": "skip"},
            ],
        }))
        backend = LinkupBackend(api_key="test-key")
        result = backend.sourced_answer("q")
        assert len(result.sources) == 1
        assert result.sources[0].url == "https://ok.com"

    @respx.mock
    def test_sourced_answer_invalid_json_raises_value_error(self) -> None:
        respx.post(LINKUP_URL).mock(return_value=httpx.Response(200, text="not-json{"))
        backend = LinkupBackend(api_key="test-key")
        with pytest.raises(ValueError, match="parse error"):
            backend.sourced_answer("q")

    @respx.mock
    def test_sourced_answer_non_dict_body_raises_value_error(self) -> None:
        respx.post(LINKUP_URL).mock(return_value=httpx.Response(200, json=[]))
        backend = LinkupBackend(api_key="test-key")
        with pytest.raises(ValueError, match="non-dict"):
            backend.sourced_answer("q")

    @respx.mock
    def test_sourced_answer_connect_error_raises_value_error(self) -> None:
        respx.post(LINKUP_URL).mock(side_effect=httpx.ConnectError("refused"))
        backend = LinkupBackend(api_key="test-key")
        with pytest.raises(ValueError, match="transport"):
            backend.sourced_answer("q")

    @respx.mock
    def test_sourced_answer_legacy_field_names_still_parsed(self) -> None:
        # Defensive: older Linkup responses used ``title``/``content`` /
        # ``description`` instead of ``name``/``snippet``. Parse both shapes.
        respx.post(LINKUP_URL).mock(return_value=httpx.Response(200, json={
            "answer": "ans",
            "sources": [
                {
                    "title": "legacy title",
                    "url": "https://x.com",
                    "content": "legacy content",
                },
            ],
        }))
        backend = LinkupBackend(api_key="test-key")
        result = backend.sourced_answer("q")
        assert len(result.sources) == 1
        assert result.sources[0].title == "legacy title"
        assert "legacy content" in result.sources[0].snippet

    @respx.mock
    def test_sourced_answer_uses_bearer_auth_header(self) -> None:
        route = respx.post(LINKUP_URL).mock(return_value=httpx.Response(200, json={
            "answer": "ans", "sources": [],
        }))
        backend = LinkupBackend(api_key="my-secret")
        backend.sourced_answer("q")
        sent = route.calls.last.request
        assert sent.headers["Authorization"] == "Bearer my-secret"


class TestLinkupSearchBackwardCompat:
    """Ensure adding sourced_answer didn't change search() behaviour."""

    @respx.mock
    def test_existing_search_still_uses_search_results_output(self) -> None:
        route = respx.post(LINKUP_URL).mock(return_value=httpx.Response(200, json={
            "results": [],
        }))
        backend = LinkupBackend(api_key="test-key")
        backend.search("q", max_results=5)
        sent = _json.loads(route.calls.last.request.content)
        assert sent["outputType"] == "searchResults"
