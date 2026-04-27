"""Multi-query expansion tests (v2.8.0 M2)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from llm_code.runtime.model_profile import ModelProfile
from llm_code.tools.research.expansion import (
    _llm_expand,
    expand,
    expand_template,
)


class TestExpandTemplateOriginalAlwaysFirst:
    def test_unmatched_query_returns_only_original(self) -> None:
        # Truly unmatched query — short, no triggers — expansion has
        # nothing to add, so we get the original alone.
        result = expand_template("opaque", max_subqueries=3)
        assert result == ("opaque",)

    def test_original_query_is_first_for_research_pattern(self) -> None:
        result = expand_template("research transformers attention", max_subqueries=3)
        assert result[0] == "research transformers attention"

    def test_max_subqueries_zero_returns_only_original(self) -> None:
        result = expand_template("research X", max_subqueries=0)
        assert result == ("research X",)


class TestResearchPattern:
    def test_research_topic_expands_to_paper_and_tutorial(self) -> None:
        result = expand_template("research transformers", max_subqueries=3)
        assert "transformers paper 2024" in result
        assert "transformers tutorial" in result

    def test_research_pattern_case_insensitive(self) -> None:
        result = expand_template("Research neural networks", max_subqueries=3)
        assert any("paper 2024" in q for q in result[1:])


class TestVsPattern:
    def test_vs_expands_to_left_and_right_comparison(self) -> None:
        result = expand_template("react vs vue", max_subqueries=3)
        assert "react comparison" in result
        assert "vue comparison" in result

    def test_vs_dot_separator_recognised(self) -> None:
        result = expand_template("python vs. ruby", max_subqueries=3)
        assert "python comparison" in result
        assert "ruby comparison" in result


class TestTimeSensitivePattern:
    def test_today_query_adds_news_and_update(self) -> None:
        result = expand_template("today's bitcoin price", max_subqueries=3)
        assert any("news" in q for q in result[1:])

    def test_chinese_today_trigger(self) -> None:
        result = expand_template("今日 比特幣 價格", max_subqueries=3)
        # Chinese today should match — at least one expansion appended.
        assert len(result) > 1


class TestHowToPattern:
    def test_how_to_expands_to_tutorial_and_guide(self) -> None:
        result = expand_template("how to deploy fastapi", max_subqueries=3)
        assert any("tutorial" in q for q in result)
        assert any("guide" in q for q in result)

    def test_chinese_howto_trigger(self) -> None:
        result = expand_template("如何 部署 fastapi", max_subqueries=3)
        assert len(result) > 1


class TestWhatIsPattern:
    def test_what_is_expands_to_definition_and_explained(self) -> None:
        result = expand_template("what is RAG?", max_subqueries=3)
        assert any("definition" in q for q in result[1:])
        assert any("explained" in q for q in result[1:])


class TestDeduplication:
    def test_no_duplicate_sub_queries_case_insensitive(self) -> None:
        # Force a pattern that might produce a duplicate; expand_template
        # de-duplicates case-insensitively.
        result = expand_template("research X", max_subqueries=10)
        lowered = [q.lower() for q in result]
        assert len(lowered) == len(set(lowered))

    def test_max_cap_truncates_excess(self) -> None:
        result = expand_template("research X", max_subqueries=2)
        assert len(result) == 2


class TestExpandDispatcher:
    @pytest.mark.asyncio
    async def test_dispatcher_off_mode_returns_singleton(self) -> None:
        profile = ModelProfile(research_query_expansion="off")
        result = await expand("research X", profile)
        assert result == ("research X",)

    @pytest.mark.asyncio
    async def test_dispatcher_template_mode_runs_template(self) -> None:
        profile = ModelProfile(
            research_query_expansion="template",
            research_max_subqueries=3,
        )
        result = await expand("research transformers", profile)
        assert result[0] == "research transformers"
        assert any("tutorial" in q for q in result[1:])

    @pytest.mark.asyncio
    async def test_dispatcher_llm_mode_no_provider_falls_back_to_template(self) -> None:
        profile = ModelProfile(
            research_query_expansion="llm",
            research_max_subqueries=3,
            tier_c_model="haiku",
        )
        result = await expand("research X", profile, provider=None)
        # No provider → template fallback runs.
        assert result[0] == "research X"

    @pytest.mark.asyncio
    async def test_dispatcher_unknown_mode_falls_through_to_template(self) -> None:
        # Unknown / typo modes degrade to template (safer than raising).
        profile = ModelProfile(
            research_query_expansion="bogus",
            research_max_subqueries=2,
        )
        result = await expand("research X", profile)
        assert result[0] == "research X"


class TestLlmExpand:
    @pytest.mark.asyncio
    async def test_llm_expand_with_provider_returns_parsed_array(self) -> None:
        # Mock provider returning a clean JSON array.
        provider = MagicMock()
        response = MagicMock()
        response.content = [MagicMock(text='["alt phrasing one", "alt phrasing two"]')]
        provider.send_message = AsyncMock(return_value=response)
        profile = ModelProfile(
            research_query_expansion="llm",
            tier_c_model="haiku",
        )
        result = await _llm_expand("research X", profile, provider=provider)
        assert result[0] == "research X"
        assert "alt phrasing one" in result
        assert "alt phrasing two" in result

    @pytest.mark.asyncio
    async def test_llm_expand_malformed_json_falls_back_to_template(self) -> None:
        provider = MagicMock()
        response = MagicMock()
        response.content = [MagicMock(text="not json at all")]
        provider.send_message = AsyncMock(return_value=response)
        profile = ModelProfile(
            research_query_expansion="llm",
            research_max_subqueries=3,
            tier_c_model="haiku",
        )
        result = await _llm_expand("research X", profile, provider=provider)
        # Template kicked in — first element original, expansions present.
        assert result[0] == "research X"

    @pytest.mark.asyncio
    async def test_llm_expand_no_tier_c_model_uses_template(self) -> None:
        provider = MagicMock()
        provider.send_message = AsyncMock()
        profile = ModelProfile(
            research_query_expansion="llm",
            tier_c_model="",  # explicitly empty
        )
        result = await _llm_expand("research X", profile, provider=provider)
        # Template fallback fires; provider must NOT have been called.
        provider.send_message.assert_not_called()
        assert result[0] == "research X"

    @pytest.mark.asyncio
    async def test_llm_expand_extracts_json_block_from_thinking_response(self) -> None:
        provider = MagicMock()
        response = MagicMock()
        response.content = [
            MagicMock(text="<think>Let me think</think>\n[\"a\", \"b\"]\n")
        ]
        provider.send_message = AsyncMock(return_value=response)
        profile = ModelProfile(
            research_query_expansion="llm",
            tier_c_model="haiku",
        )
        result = await _llm_expand("research X", profile, provider=provider)
        assert "a" in result
        assert "b" in result

    @pytest.mark.asyncio
    async def test_llm_expand_dedupes_against_original(self) -> None:
        provider = MagicMock()
        response = MagicMock()
        response.content = [MagicMock(text='["research X", "alt"]')]
        provider.send_message = AsyncMock(return_value=response)
        profile = ModelProfile(
            research_query_expansion="llm",
            tier_c_model="haiku",
        )
        result = await _llm_expand("research X", profile, provider=provider)
        # "research X" duplicate filtered; only "alt" appended.
        assert result == ("research X", "alt")

    @pytest.mark.asyncio
    async def test_llm_expand_max_subqueries_zero(self) -> None:
        provider = MagicMock()
        provider.send_message = AsyncMock()
        profile = ModelProfile(
            research_query_expansion="llm",
            tier_c_model="haiku",
        )
        result = await _llm_expand(
            "X", profile, provider=provider, max_subqueries=0,
        )
        assert result == ("X",)
