"""Tier C classifier: a clean 'none' answer must be authoritative and must
NOT fall through to the substring fallback, even if skill names appear in
the reasoning block."""
from __future__ import annotations

import pytest

from llm_code.runtime.skill_router import _classify_with_llm_debug


class _FakeSkill:
    def __init__(self, name: str, description: str = "") -> None:
        self.name = name
        self.description = description
        self.keywords: tuple[str, ...] = ()
        self.trigger: str = ""


class _FakeTextBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.content = (_FakeTextBlock(text),)


class _FakeProvider:
    def __init__(self, response_text: str) -> None:
        self._response_text = response_text

    async def send_message(self, request) -> _FakeResponse:
        return _FakeResponse(self._response_text)


@pytest.mark.asyncio
async def test_clean_none_answer_returns_none_even_with_skill_names_in_thinking() -> None:
    """The reasoning model discusses brainstorming in its thinking block,
    then cleanly answers 'none'. Router must respect the clean answer."""
    skills = [
        _FakeSkill("brainstorming", "For creative exploration"),
        _FakeSkill("debugging", "For finding root causes"),
    ]
    raw = (
        "<think>\n"
        "The user is asking for news. This is not brainstorming "
        "since brainstorming is for creative tasks. Not debugging either.\n"
        "</think>\n"
        "Answer: none"
    )
    provider = _FakeProvider(raw)
    matched, _ = await _classify_with_llm_debug(
        "給我今日熱門新聞三則", skills, provider, model="fake-model"
    )
    assert matched is None, (
        f"expected None for clean 'none' answer but got {matched!r}"
    )


@pytest.mark.asyncio
async def test_clean_skill_name_still_returns_that_skill() -> None:
    """Regression: the clean-answer happy path must keep working."""
    skills = [_FakeSkill("brainstorming"), _FakeSkill("debugging")]
    provider = _FakeProvider("Answer: brainstorming")
    matched, _ = await _classify_with_llm_debug(
        "help me explore ideas", skills, provider, model="fake-model"
    )
    assert matched == "brainstorming"
