"""Tests for opt-in RateLimitHandler wiring into the providers (C3b).

Both providers accept an optional ``rate_handler`` kwarg. When present
the request loop routes through :func:`run_with_rate_limit`; when
``None`` (the default) they keep the pre-existing ``_post_with_retry``
behaviour untouched — the goal is an additive wiring change that
doesn't perturb any of the 5000+ existing tests.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from llm_code.api.errors import ProviderRateLimitError
from llm_code.api.rate_limiter import (
    RateLimitHandler,
    RequestKind,
    provider_taxonomy_anthropic,
    provider_taxonomy_openai_compat,
)


def _ok_response(json_body: dict | None = None) -> httpx.Response:
    """Build a fake 200 response whose json() returns ``json_body``."""
    return httpx.Response(
        200,
        json=json_body or {"id": "x", "choices": [{"message": {"content": ""}}]},
    )


# ---------- OpenAICompatProvider ----------


class TestOpenAICompatOptInWiring:
    def test_accepts_rate_handler_kwarg(self) -> None:
        from llm_code.api.openai_compat import OpenAICompatProvider

        handler = RateLimitHandler(
            request_kind=RequestKind.FOREGROUND,
            taxonomy=provider_taxonomy_openai_compat(),
        )
        provider = OpenAICompatProvider(
            base_url="http://fake", api_key="k",
            model_name="gpt-4o",
            rate_handler=handler,
        )
        assert provider._rate_handler is handler

    def test_default_constructor_has_no_rate_handler(self) -> None:
        """Backward compatibility: the pre-existing constructor call
        sites (no rate_handler) keep rate_handler=None, so the legacy
        _post_with_retry path still runs."""
        from llm_code.api.openai_compat import OpenAICompatProvider

        provider = OpenAICompatProvider(
            base_url="http://fake", api_key="k", model_name="gpt-4o",
        )
        assert provider._rate_handler is None

    @pytest.mark.asyncio
    async def test_rate_handler_retries_then_succeeds(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from llm_code.api.openai_compat import OpenAICompatProvider

        handler = RateLimitHandler(
            request_kind=RequestKind.FOREGROUND,
            taxonomy=provider_taxonomy_openai_compat(),
        )
        provider = OpenAICompatProvider(
            base_url="http://fake", api_key="k",
            model_name="gpt-4o",
            rate_handler=handler,
        )

        # First call rate-limits, second succeeds. Because the handler's
        # backoff uses retry_after when present, supply 0s so the test
        # is fast without monkey-patching asyncio.sleep.
        call_count = {"n": 0}

        async def fake_post(url: str, *, json: Any) -> httpx.Response:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise ProviderRateLimitError("429", retry_after=0.0)
            return _ok_response()

        monkeypatch.setattr(provider._client, "post", AsyncMock(side_effect=fake_post))

        response = await provider._post_with_retry({"model": "x", "messages": []})
        assert response.status_code == 200
        assert call_count["n"] == 2
        # Handler reset on success
        assert handler.attempt == 0

    @pytest.mark.asyncio
    async def test_legacy_path_when_handler_absent(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without a rate_handler the provider must NOT call the new
        ``run_with_rate_limit`` wrapper — the legacy loop handles retry."""
        from llm_code.api.openai_compat import OpenAICompatProvider

        provider = OpenAICompatProvider(
            base_url="http://fake", api_key="k", model_name="gpt-4o",
        )
        called = {"wrapper": False}

        async def spy_wrapper(*args, **kwargs):  # noqa: ARG001
            called["wrapper"] = True
            raise AssertionError("wrapper must not run without a rate_handler")

        monkeypatch.setattr(
            "llm_code.api.openai_compat.run_with_rate_limit",
            spy_wrapper,
            raising=False,
        )

        async def fake_post(url: str, *, json: Any) -> httpx.Response:
            return _ok_response()

        monkeypatch.setattr(provider._client, "post", AsyncMock(side_effect=fake_post))

        response = await provider._post_with_retry({"model": "x", "messages": []})
        assert response.status_code == 200
        assert called["wrapper"] is False


# ---------- AnthropicProvider ----------


class TestAnthropicOptInWiring:
    def test_accepts_rate_handler_kwarg(self) -> None:
        from llm_code.api.anthropic_provider import AnthropicProvider

        handler = RateLimitHandler(
            request_kind=RequestKind.FOREGROUND,
            taxonomy=provider_taxonomy_anthropic(),
        )
        provider = AnthropicProvider(
            api_key="sk-test", model_name="claude-sonnet-4-6",
            rate_handler=handler,
        )
        assert provider._rate_handler is handler

    def test_default_constructor_has_no_rate_handler(self) -> None:
        from llm_code.api.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider(
            api_key="sk-test", model_name="claude-sonnet-4-6",
        )
        assert provider._rate_handler is None

    @pytest.mark.asyncio
    async def test_rate_handler_retries_then_succeeds(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from llm_code.api.anthropic_provider import AnthropicProvider

        handler = RateLimitHandler(
            request_kind=RequestKind.FOREGROUND,
            taxonomy=provider_taxonomy_anthropic(),
        )
        provider = AnthropicProvider(
            api_key="sk-test", model_name="claude-sonnet-4-6",
            rate_handler=handler,
        )

        call_count = {"n": 0}

        async def fake_post(url: str, *, json: Any) -> httpx.Response:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise ProviderRateLimitError("429", retry_after=0.0)
            return _ok_response({"id": "x", "content": []})

        monkeypatch.setattr(provider._client, "post", AsyncMock(side_effect=fake_post))

        response = await provider._post_with_retry({"model": "x"})
        assert response.status_code == 200
        assert call_count["n"] == 2
        assert handler.attempt == 0
