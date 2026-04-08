"""Tier C substring fallback: require >=2 mentions AND margin >=2 over runner-up
before accepting a match. A single mention (or a tie) must not win."""
from __future__ import annotations

import pytest

from llm_code.runtime.skill_router import _classify_with_llm_debug


class _FakeSkill:
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = ""
        self.keywords: tuple[str, ...] = ()
        self.trigger: str = ""


class _FakeTextBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.content = (_FakeTextBlock(text),)


class _FakeProvider:
    def __init__(self, text: str) -> None:
        self._text = text

    async def send_message(self, request) -> _FakeResponse:
        return _FakeResponse(self._text)


@pytest.mark.asyncio
async def test_single_mention_in_thinking_does_not_win() -> None:
    skills = [_FakeSkill("brainstorming"), _FakeSkill("debugging")]
    raw = (
        "<think>considering brainstorming vs other options</think>\n"
        "Answer: [unclear]"
    )
    provider = _FakeProvider(raw)
    matched, _ = await _classify_with_llm_debug(
        "給我今日熱門新聞三則", skills, provider, model="fake"
    )
    assert matched is None


@pytest.mark.asyncio
async def test_tied_mentions_does_not_win() -> None:
    skills = [_FakeSkill("brainstorming"), _FakeSkill("debugging")]
    raw = (
        "<think>brainstorming? debugging? brainstorming vs debugging?</think>"
    )
    provider = _FakeProvider(raw)
    matched, _ = await _classify_with_llm_debug(
        "ambiguous", skills, provider, model="fake"
    )
    assert matched is None


@pytest.mark.asyncio
async def test_dominant_skill_still_wins_with_margin() -> None:
    skills = [_FakeSkill("brainstorming"), _FakeSkill("debugging")]
    raw = (
        "<think>This is brainstorming. Let me use brainstorming for this. "
        "Brainstorming is ideal here.</think>"
    )
    provider = _FakeProvider(raw)
    matched, _ = await _classify_with_llm_debug(
        "creative exploration", skills, provider, model="fake"
    )
    assert matched == "brainstorming"
