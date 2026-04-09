"""Wave2-1b: Retry-After header parsing + ProviderTimeoutError.

The audit found two concrete gaps in llm-code's retry path:

1. 429 responses carried no Retry-After parsing — ``_post_with_retry``
   always used ``2 ** attempt`` even when the provider said exactly
   how long to wait. This caused premature retry hits on the rate-
   limited provider before its reset window expired.

2. ``httpx.ReadTimeout`` / ``ConnectTimeout`` / ``WriteTimeout`` /
   ``PoolTimeout`` fell through ``_post_with_retry`` uncaught and
   became generic ``Exception`` in conversation.py — skipping the
   retry budget entirely instead of getting exponential backoff.

This test module pins both fixes at the unit level without spinning
up an httpx server. The provider's retry loop is exercised via a
stubbed ``_client.post`` that returns / raises on demand.
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from llm_code.api.errors import (
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from llm_code.api.openai_compat import (
    OpenAICompatProvider,
    _MAX_RETRY_AFTER_SECONDS,
    _parse_retry_after_header,
)


# ---------- _parse_retry_after_header ----------

def test_parse_retry_after_returns_none_for_missing() -> None:
    assert _parse_retry_after_header(None) is None
    assert _parse_retry_after_header("") is None


def test_parse_retry_after_parses_delta_seconds() -> None:
    assert _parse_retry_after_header("30") == 30.0
    assert _parse_retry_after_header("2.5") == 2.5
    # Leading/trailing whitespace tolerated
    assert _parse_retry_after_header("  15  ") == 15.0


def test_parse_retry_after_rejects_garbage() -> None:
    """HTTP spec also allows an HTTP-date form (e.g. 'Wed, 21 Oct
    2026 07:28:00 GMT') but no real LLM provider uses it on 429.
    We only handle delta-seconds; anything unparseable returns None
    so the caller falls back to exponential backoff."""
    assert _parse_retry_after_header("not-a-number") is None
    assert _parse_retry_after_header("Wed, 21 Oct 2026 07:28:00 GMT") is None


def test_parse_retry_after_rejects_non_positive() -> None:
    """A zero or negative Retry-After is nonsensical; treat it as
    absent so exponential backoff kicks in."""
    assert _parse_retry_after_header("0") is None
    assert _parse_retry_after_header("-5") is None


def test_parse_retry_after_clamps_to_max_cap() -> None:
    """A misbehaving proxy returning 'Retry-After: 86400' must not
    wedge the runtime for a day. The cap is conservative (60s)."""
    huge = _parse_retry_after_header("86400")
    assert huge == _MAX_RETRY_AFTER_SECONDS
    assert huge == 60.0


# ---------- _post_with_retry honors Retry-After over 2**attempt ----------

class _ScriptedClient:
    """Stub for httpx.AsyncClient that replays a scripted sequence.

    Each entry in ``script`` is either a ready-made httpx.Response,
    an exception instance to raise, or a callable that produces one.
    Used to avoid spinning up an actual HTTP server in unit tests.
    """

    def __init__(self, script: list[Any]) -> None:
        self._script = list(script)
        self.calls = 0

    async def post(self, url: str, json: dict) -> httpx.Response:
        self.calls += 1
        if not self._script:
            raise RuntimeError("scripted client ran out of responses")
        entry = self._script.pop(0)
        if callable(entry) and not isinstance(entry, httpx.Response):
            entry = entry()
        if isinstance(entry, BaseException):
            raise entry
        return entry


def _make_provider(script: list[Any]) -> OpenAICompatProvider:
    provider = OpenAICompatProvider(base_url="http://localhost:0", api_key="key")
    provider._client = _ScriptedClient(script)  # type: ignore[assignment]
    return provider


def _json_response(status: int, *, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        headers=headers or {},
        content=b'{"error":{"message":"rate limited"}}',
    )


def _ok_response() -> httpx.Response:
    return httpx.Response(
        status_code=200,
        headers={"content-type": "application/json"},
        content=b'{"choices":[{"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1}}',
    )


@pytest.mark.asyncio
async def test_rate_limit_honors_retry_after_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 429 with Retry-After: 3.5 must cause the retry loop to
    sleep for 3.5 seconds, not 2**attempt (= 1)."""
    sleeps: list[float] = []

    async def _record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("llm_code.api.openai_compat.asyncio.sleep", _record_sleep)

    provider = _make_provider([
        _json_response(429, headers={"Retry-After": "3.5"}),
        _ok_response(),
    ])
    await provider._post_with_retry({"model": "test"})
    assert sleeps == [3.5]


@pytest.mark.asyncio
async def test_rate_limit_falls_back_to_exponential_without_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider didn't send Retry-After — 2**0 = 1 second on first
    retry, matching the pre-P5 behavior."""
    sleeps: list[float] = []

    async def _record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("llm_code.api.openai_compat.asyncio.sleep", _record_sleep)

    provider = _make_provider([
        _json_response(429),
        _ok_response(),
    ])
    await provider._post_with_retry({"model": "test"})
    assert sleeps == [1.0]


@pytest.mark.asyncio
async def test_rate_limit_clamps_hostile_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A proxy returning 'Retry-After: 999999' must be capped to
    _MAX_RETRY_AFTER_SECONDS so the runtime does not wedge."""
    sleeps: list[float] = []

    async def _record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("llm_code.api.openai_compat.asyncio.sleep", _record_sleep)

    provider = _make_provider([
        _json_response(429, headers={"Retry-After": "999999"}),
        _ok_response(),
    ])
    await provider._post_with_retry({"model": "test"})
    assert sleeps == [_MAX_RETRY_AFTER_SECONDS]


@pytest.mark.asyncio
async def test_rate_limit_error_carries_retry_after_attribute() -> None:
    """A 429 that cannot be retried (exhausted budget) must re-raise
    with retry_after attached so any outer handler can inspect it."""
    provider = _make_provider([
        _json_response(429, headers={"Retry-After": "7"}),
        _json_response(429, headers={"Retry-After": "9"}),
        _json_response(429, headers={"Retry-After": "11"}),
    ])
    provider._max_retries = 2  # budget = 2 retries; 3rd attempt re-raises
    with pytest.raises(ProviderRateLimitError) as excinfo:
        await provider._post_with_retry({"model": "test"})
    assert excinfo.value.retry_after == 11.0


# ---------- Timeout flavors ----------

@pytest.mark.asyncio
async def test_read_timeout_becomes_retryable_provider_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """httpx.ReadTimeout used to fall through _post_with_retry as a
    bare Exception. Now it's wrapped as ProviderTimeoutError and
    retried with exponential backoff."""
    sleeps: list[float] = []

    async def _record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("llm_code.api.openai_compat.asyncio.sleep", _record_sleep)

    provider = _make_provider([
        httpx.ReadTimeout("read timed out"),
        _ok_response(),
    ])
    await provider._post_with_retry({"model": "test"})
    assert sleeps == [1.0]


@pytest.mark.asyncio
async def test_connect_timeout_is_also_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    async def _record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("llm_code.api.openai_compat.asyncio.sleep", _record_sleep)

    provider = _make_provider([
        httpx.ConnectTimeout("connect timed out"),
        _ok_response(),
    ])
    await provider._post_with_retry({"model": "test"})
    assert sleeps == [1.0]


@pytest.mark.asyncio
async def test_all_timeout_flavors_raise_provider_timeout_after_budget() -> None:
    """Exhausted retry budget: the loop must re-raise a
    ProviderTimeoutError (is_retryable=True), not the raw httpx
    exception."""
    provider = _make_provider([
        httpx.ReadTimeout("1"),
        httpx.WriteTimeout("2"),
        httpx.PoolTimeout("3"),
    ])
    provider._max_retries = 2
    with pytest.raises(ProviderTimeoutError) as excinfo:
        await provider._post_with_retry({"model": "test"})
    assert excinfo.value.is_retryable is True


@pytest.mark.asyncio
async def test_auth_error_still_not_retried() -> None:
    """Sanity: wave2-1b must not accidentally retry 401. The
    ProviderAuthError path from wave2-3 must still work."""
    provider = _make_provider([
        _json_response(401),
    ])
    with pytest.raises(ProviderAuthError):
        await provider._post_with_retry({"model": "test"})
    assert provider._client.calls == 1  # type: ignore[attr-defined]


# ----- Tool-call-parser fast-fail (screenshot 3 field report) -----

from llm_code.api.errors import ProviderError  # noqa: E402


@pytest.mark.asyncio
async def test_tool_call_parser_error_is_non_retryable(monkeypatch) -> None:
    """When the server returns an error mentioning 'tool-call-parser'
    or 'tool choice', _raise_for_status raises ProviderError with
    is_retryable=False. The retry loop should NOT re-attempt — the
    outer fallback in conversation.py will switch to XML mode and
    retry with tools=[]. Without this fast-fail, _post_with_retry
    burns ~30s on exponential backoff before surfacing the error."""
    sleeps: list[float] = []

    async def _record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("llm_code.api.openai_compat.asyncio.sleep", _record_sleep)

    provider = _make_provider([
        httpx.Response(
            status_code=400,
            content=b'{"error": {"message": "tool-call-parser not configured"}}',
        ),
    ])
    with pytest.raises(ProviderError) as excinfo:
        await provider._post_with_retry({"model": "test"})
    assert excinfo.value.is_retryable is False
    assert "tool-call-parser" in str(excinfo.value)
    # CRITICAL: zero sleeps — no retry budget was spent
    assert sleeps == []
    # CRITICAL: exactly one call to the server
    assert provider._client.calls == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_tool_choice_error_is_also_non_retryable(monkeypatch) -> None:
    """Same as above but the other known error message variant."""
    sleeps: list[float] = []
    async def _record_sleep(seconds: float) -> None:
        sleeps.append(seconds)
    monkeypatch.setattr("llm_code.api.openai_compat.asyncio.sleep", _record_sleep)

    provider = _make_provider([
        httpx.Response(
            status_code=400,
            content=b'{"error": {"message": "invalid tool choice parameter"}}',
        ),
    ])
    with pytest.raises(ProviderError) as excinfo:
        await provider._post_with_retry({"model": "test"})
    assert excinfo.value.is_retryable is False
    assert sleeps == []


@pytest.mark.asyncio
async def test_plain_400_without_tool_message_still_retryable(monkeypatch) -> None:
    """A generic 4xx (not tool-call related) stays on the retry path
    so existing behavior for other 4xx errors isn't affected."""
    sleeps: list[float] = []
    async def _record_sleep(seconds: float) -> None:
        sleeps.append(seconds)
    monkeypatch.setattr("llm_code.api.openai_compat.asyncio.sleep", _record_sleep)

    provider = _make_provider([
        httpx.Response(status_code=400, content=b'{"error": {"message": "bad request"}}'),
        _ok_response(),
    ])
    await provider._post_with_retry({"model": "test"})
    # Should have retried at least once (1s backoff)
    assert sleeps == [1.0]
