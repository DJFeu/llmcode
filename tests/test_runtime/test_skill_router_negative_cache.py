"""Regression guard for the SkillRouter "Tier C runs twice per turn"
perf bug observed in the 2026-04-09 Qwen3.5 field report chain.

The scenario: route_async is called TWICE per turn — once from the
TUI for display (`app.py:1426`) and once from the runtime for
prompt injection (`conversation.py:1036`). Before the fix, negative
Tier C results (no skill matched) were NOT cached, so every CJK
query that went through Tier C paid the 5-15s LLM classifier round-
trip TWICE per turn.

The fix: route_async checks the cache FIRST (including negative
results) before dispatching to tier A/B, and writes the Tier C
negative result back to the cache so the second call is a hit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from llm_code.runtime.config import SkillRouterConfig
from llm_code.runtime.skill_router import SkillRouter


@dataclass
class _FakeSkill:
    name: str
    description: str = ""
    content: str = ""
    keywords: frozenset[str] = field(default_factory=frozenset)


class _CountingProvider:
    """Async provider double that counts how many times Tier C
    called it and what it returned."""

    def __init__(self, classification_response: str = "NONE") -> None:
        self.call_count = 0
        self._response = classification_response

    async def send_message(self, request: Any) -> Any:
        self.call_count += 1
        # Return a minimal MessageResponse-shaped object
        from llm_code.api.types import MessageResponse, TextBlock, TokenUsage
        return MessageResponse(
            content=(TextBlock(text=self._response),),
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason="stop",
        )


def _make_router_with_tier_c(provider: _CountingProvider) -> SkillRouter:
    """Build a SkillRouter with Tier C force-enabled and a fake
    provider so we can count Tier C LLM calls."""
    return SkillRouter(
        skills=[
            _FakeSkill(name="alpha", description="skill alpha handles foo"),
            _FakeSkill(name="beta", description="skill beta handles bar"),
        ],
        config=SkillRouterConfig(
            enabled=True, tier_a=True, tier_b=False,
            tier_c=True, tier_c_auto_for_cjk=False,
        ),
        provider=provider,
        model="test-model",
    )


@pytest.mark.asyncio
async def test_tier_c_negative_result_is_cached() -> None:
    """The first call runs Tier C and caches the negative result;
    the second call must hit the cache and NOT call the provider
    again. This is the core perf fix — without it, the LLM round-
    trip runs twice per CJK turn."""
    provider = _CountingProvider(classification_response="NONE")
    router = _make_router_with_tier_c(provider)

    result1 = await router.route_async("今日新聞三則")
    assert result1 == []
    assert provider.call_count == 1  # First Tier C call

    # Second call with same query — must be cached
    result2 = await router.route_async("今日新聞三則")
    assert result2 == []
    assert provider.call_count == 1  # Still 1, no re-run


@pytest.mark.asyncio
async def test_tier_c_positive_result_cached_as_before() -> None:
    """Pre-existing positive-cache behavior must keep working.
    Returning a matched skill name caches the result; second call
    hits the cache."""
    provider = _CountingProvider(classification_response="alpha")
    router = _make_router_with_tier_c(provider)

    result1 = await router.route_async("some query")
    assert len(result1) == 1
    assert result1[0].name == "alpha"
    assert provider.call_count == 1

    result2 = await router.route_async("some query")
    assert len(result2) == 1
    assert result2[0].name == "alpha"
    assert provider.call_count == 1  # Cached


@pytest.mark.asyncio
async def test_different_queries_miss_cache() -> None:
    """Cache is keyed by the query text, so different queries run
    Tier C independently. Sanity check that we didn't over-cache."""
    provider = _CountingProvider(classification_response="NONE")
    router = _make_router_with_tier_c(provider)

    await router.route_async("query one")
    await router.route_async("query two")
    assert provider.call_count == 2  # Two distinct queries, two Tier C runs


@pytest.mark.asyncio
async def test_tier_c_not_called_when_provider_missing() -> None:
    """If no provider is wired, Tier C shouldn't be attempted —
    and the negative result should still be cached so the second
    call is instant."""
    router = SkillRouter(
        skills=[_FakeSkill(name="alpha", description="handles foo")],
        config=SkillRouterConfig(
            enabled=True, tier_a=True, tier_b=False,
            tier_c=True, tier_c_auto_for_cjk=False,
        ),
        provider=None,  # ← no provider
        model="test-model",
    )

    result1 = await router.route_async("some query")
    result2 = await router.route_async("some query")
    assert result1 == []
    assert result2 == []


@pytest.mark.asyncio
async def test_sync_tier_a_hit_still_cached_for_async_reuse() -> None:
    """If Tier A (sync) matches first, the positive result is
    cached and a subsequent async call hits the cache without
    running Tier C even for CJK queries."""
    provider = _CountingProvider(classification_response="NONE")
    router = SkillRouter(
        skills=[
            _FakeSkill(
                name="news_fetcher",
                description="fetches news",
                # Keywords that match the query so tier A hits
                keywords=frozenset({"news", "新聞"}),
            ),
        ],
        config=SkillRouterConfig(
            enabled=True, tier_a=True, tier_b=False,
            tier_c=True, tier_c_auto_for_cjk=True,
        ),
        provider=provider,
        model="test-model",
    )

    # First call — tier A should match via the 新聞 keyword
    result1 = await router.route_async("今日新聞三則")
    # Second call — must hit cache, NOT invoke Tier C provider
    result2 = await router.route_async("今日新聞三則")
    assert provider.call_count == 0  # Tier A won, Tier C never fired


@pytest.mark.asyncio
async def test_cache_persists_empty_from_sync_route_path() -> None:
    """Edge case: the sync ``route`` method already caches empty
    results from tier A/B misses. route_async must honor that
    cache on the second call instead of re-running Tier C just
    because ``if result:`` is False."""
    provider = _CountingProvider(classification_response="NONE")
    # Router with Tier C DISABLED so route_async falls through
    # with no Tier C attempt on the first call
    router = SkillRouter(
        skills=[_FakeSkill(name="alpha", description="")],
        config=SkillRouterConfig(
            enabled=True, tier_a=True, tier_b=False,
            tier_c=False, tier_c_auto_for_cjk=False,
        ),
        provider=provider,
        model="test-model",
    )

    result1 = await router.route_async("no match query")
    result2 = await router.route_async("no match query")
    assert result1 == []
    assert result2 == []
    assert provider.call_count == 0  # Tier C off, provider never called
