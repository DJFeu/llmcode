"""Tier C auto-fallback for CJK prompts.

When a user types Chinese (or any CJK) and Tier A/B both miss because
skill descriptions are English, the LLM classifier should kick in
automatically without requiring the user to enable ``tier_c`` manually.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from llm_code.runtime import skill_router_cache as _src
from llm_code.runtime.config import SkillRouterConfig
from llm_code.runtime.skill_router import SkillRouter


@pytest.fixture(autouse=True)
def _isolated_persistent_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Prevent persistent cache cross-contamination between tests."""
    monkeypatch.setattr(_src, "_CACHE_PATH", tmp_path / "skill_router_cache.json")


@dataclass(frozen=True)
class _StubSkill:
    name: str
    description: str
    tags: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()


@pytest.fixture
def skills() -> list[_StubSkill]:
    return [
        _StubSkill(
            name="brainstorming",
            description="Generate ideas and design new features collaboratively",
        ),
        _StubSkill(
            name="debugging",
            description="Diagnose errors and trace bugs in code",
        ),
    ]


def _make_provider(answer: str) -> AsyncMock:
    """Stub provider returning a fake message with ``answer`` as text."""
    provider = AsyncMock()

    class _Block:
        def __init__(self, text: str) -> None:
            self.text = text

    class _Resp:
        def __init__(self, text: str) -> None:
            self.content = (_Block(text),)

    provider.send_message = AsyncMock(return_value=_Resp(answer))
    return provider


@pytest.mark.asyncio
async def test_cjk_prompt_auto_falls_through_to_tier_c(skills: list[_StubSkill]) -> None:
    """Chinese prompt with no Tier A/B match → Tier C fires automatically."""
    cfg = SkillRouterConfig(tier_c=False, tier_c_auto_for_cjk=True)
    provider = _make_provider("brainstorming")
    router = SkillRouter(skills=skills, config=cfg, provider=provider, model="test-model")

    result = await router.route_async("我想設計一個工具")

    assert provider.send_message.await_count == 1
    assert [s.name for s in result] == ["brainstorming"]


@pytest.mark.asyncio
async def test_english_prompt_does_not_trigger_tier_c_when_disabled(
    skills: list[_StubSkill],
) -> None:
    """Pure-English prompt with Tier A/B miss should NOT call the LLM."""
    cfg = SkillRouterConfig(tier_c=False, tier_c_auto_for_cjk=True)
    provider = _make_provider("brainstorming")
    router = SkillRouter(skills=skills, config=cfg, provider=provider, model="test-model")

    result = await router.route_async("xyzqq nonsense unmatched")

    assert provider.send_message.await_count == 0
    assert result == []


@pytest.mark.asyncio
async def test_cjk_auto_fallback_can_be_disabled(skills: list[_StubSkill]) -> None:
    """When ``tier_c_auto_for_cjk=False`` the CJK prompt should not trigger Tier C."""
    cfg = SkillRouterConfig(tier_c=False, tier_c_auto_for_cjk=False)
    provider = _make_provider("brainstorming")
    router = SkillRouter(skills=skills, config=cfg, provider=provider, model="test-model")

    result = await router.route_async("我想設計一個工具")

    assert provider.send_message.await_count == 0
    assert result == []


@pytest.mark.asyncio
async def test_tier_a_match_short_circuits_tier_c(skills: list[_StubSkill]) -> None:
    """English keyword that matches Tier A should not invoke the LLM."""
    cfg = SkillRouterConfig(tier_c=False, tier_c_auto_for_cjk=True)
    provider = _make_provider("brainstorming")
    router = SkillRouter(skills=skills, config=cfg, provider=provider, model="test-model")

    result = await router.route_async("brainstorming new design ideas")

    assert provider.send_message.await_count == 0
    assert any(s.name == "brainstorming" for s in result)
