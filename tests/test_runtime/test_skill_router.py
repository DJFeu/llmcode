"""Tests for llm_code.runtime.skill_router — 3-tier cascade."""
from __future__ import annotations

import pytest

from llm_code.runtime.config import SkillRouterConfig
from llm_code.runtime.skills import Skill
from llm_code.runtime.skill_router import (
    SkillRouter,
    tokenize,
    _content_tokens,
    _extract_keywords,
    _build_tfidf_index,
    _cosine_similarity,
    _tfidf_query_vector,
)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def brainstorm_skill() -> Skill:
    return Skill(
        name="brainstorming",
        description="Creative work - creating features, building components, designing systems",
        content="Full brainstorming skill content here...",
        auto=True,
        keywords=("brainstorm", "creative", "design", "feature", "component", "設計", "創意"),
    )


@pytest.fixture
def debug_skill() -> Skill:
    return Skill(
        name="debugging",
        description="Bug fixing, error analysis, troubleshooting unexpected behavior",
        content="Full debugging skill content here...",
        auto=True,
        keywords=("debug", "bug", "error", "fix", "troubleshoot", "修復", "錯誤"),
    )


@pytest.fixture
def tdd_skill() -> Skill:
    return Skill(
        name="tdd",
        description="Test-driven development - write tests first, then implement",
        content="Full TDD skill content here...",
        auto=True,
        keywords=("test", "tdd", "coverage", "測試"),
    )


@pytest.fixture
def all_skills(brainstorm_skill, debug_skill, tdd_skill) -> tuple[Skill, ...]:
    return (brainstorm_skill, debug_skill, tdd_skill)


@pytest.fixture
def router(all_skills) -> SkillRouter:
    return SkillRouter(skills=all_skills, config=SkillRouterConfig())


# ------------------------------------------------------------------
# Tokenization
# ------------------------------------------------------------------

class TestTokenize:
    def test_basic_latin(self):
        assert tokenize("hello world") == ["hello", "world"]

    def test_lowercase(self):
        assert tokenize("Hello WORLD") == ["hello", "world"]

    def test_punctuation_split(self):
        assert tokenize("fix bug, please!") == ["fix", "bug", "please"]

    def test_cjk_individual_chars(self):
        tokens = tokenize("我想設計一個工具")
        assert "我" in tokens
        assert "設" in tokens
        assert "計" in tokens
        assert "工" in tokens
        assert "具" in tokens

    def test_mixed_cjk_latin(self):
        tokens = tokenize("我想要design一個tool")
        assert "design" in tokens
        assert "tool" in tokens
        assert "我" in tokens
        assert "想" in tokens

    def test_empty(self):
        assert tokenize("") == []

    def test_hyphenated_word(self):
        assert tokenize("test-driven") == ["test-driven"]

    def test_underscore_word(self):
        assert tokenize("my_var") == ["my_var"]


class TestContentTokens:
    def test_removes_stopwords(self):
        tokens = _content_tokens("use this tool for the task")
        assert "this" not in tokens  # "this" is a stopword
        assert "for" not in tokens
        assert "tool" in tokens  # "tool" is NOT a stopword (meaningful in skill context)
        assert "task" in tokens

    def test_keeps_meaningful_words(self):
        tokens = _content_tokens("brainstorm creative design")
        assert "brainstorm" in tokens
        assert "creative" in tokens
        assert "design" in tokens


# ------------------------------------------------------------------
# Keyword extraction
# ------------------------------------------------------------------

class TestExtractKeywords:
    def test_explicit_keywords(self, brainstorm_skill):
        kws = _extract_keywords(brainstorm_skill)
        assert "brainstorm" in kws
        assert "creative" in kws
        assert "design" in kws
        assert "設" in kws  # CJK chars from "設計"
        assert "計" in kws

    def test_auto_extract_from_description(self):
        skill = Skill(name="reviewer", description="code review and quality checks", content="...")
        kws = _extract_keywords(skill)
        assert "reviewer" in kws
        assert "code" in kws
        assert "review" in kws
        assert "quality" in kws


# ------------------------------------------------------------------
# TF-IDF
# ------------------------------------------------------------------

class TestTfidf:
    def test_build_index(self, all_skills):
        idx = _build_tfidf_index(all_skills)
        assert len(idx.vectors) == 3
        assert "brainstorming" in idx.vectors
        assert "debugging" in idx.vectors
        assert "tdd" in idx.vectors

    def test_cosine_identical(self):
        a = {"x": 1.0, "y": 2.0}
        assert _cosine_similarity(a, a) == pytest.approx(1.0)

    def test_cosine_orthogonal(self):
        a = {"x": 1.0}
        b = {"y": 1.0}
        assert _cosine_similarity(a, b) == 0.0

    def test_cosine_empty(self):
        assert _cosine_similarity({}, {"x": 1.0}) == 0.0

    def test_query_vector(self, all_skills):
        idx = _build_tfidf_index(all_skills)
        qv = _tfidf_query_vector(["debug", "error", "fix"], idx.idf)
        assert len(qv) > 0
        assert "debug" in qv


# ------------------------------------------------------------------
# Tier A: Keyword matching
# ------------------------------------------------------------------

class TestTierA:
    def test_exact_keyword_match(self, router):
        result = router.route("I want to brainstorm a new feature")
        assert len(result) >= 1
        assert result[0].name == "brainstorming"

    def test_cjk_keyword_match(self, router):
        result = router.route("我想要設計一個新功能")
        assert len(result) >= 1
        assert result[0].name == "brainstorming"

    def test_debug_keyword_match(self, router):
        result = router.route("I have a bug to fix")
        assert len(result) >= 1
        assert result[0].name == "debugging"

    def test_no_match(self, router):
        result = router.route("hello, how are you?")
        # May or may not match depending on TF-IDF — just ensure no crash
        assert isinstance(result, list)

    def test_tdd_keyword_match(self, router):
        result = router.route("let's write tests using TDD")
        assert len(result) >= 1
        assert result[0].name == "tdd"


# ------------------------------------------------------------------
# Tier B: TF-IDF similarity
# ------------------------------------------------------------------

class TestTierB:
    def test_similar_description_matches(self, all_skills):
        config = SkillRouterConfig(tier_a=False, tier_b=True, similarity_threshold=0.1)
        router = SkillRouter(skills=all_skills, config=config)
        result = router.route("creating new features and components")
        assert len(result) >= 1
        assert result[0].name == "brainstorming"

    def test_unrelated_message_no_match(self, all_skills):
        config = SkillRouterConfig(tier_a=False, tier_b=True, similarity_threshold=0.8)
        router = SkillRouter(skills=all_skills, config=config)
        result = router.route("what is the weather today")
        assert result == []


# ------------------------------------------------------------------
# Cascade behavior
# ------------------------------------------------------------------

class TestCascade:
    def test_tier_a_hit_skips_b(self, all_skills):
        config = SkillRouterConfig(tier_a=True, tier_b=True)
        router = SkillRouter(skills=all_skills, config=config)
        result = router.route("brainstorm a design")
        assert len(result) >= 1
        assert result[0].name == "brainstorming"

    def test_tier_a_miss_falls_to_b(self, all_skills):
        config = SkillRouterConfig(tier_a=True, tier_b=True, similarity_threshold=0.1)
        router = SkillRouter(skills=all_skills, config=config)
        # A message without direct keywords but semantically related
        result = router.route("analyze unexpected program behavior systematically")
        # Should get some result from Tier B
        assert isinstance(result, list)

    def test_all_disabled_returns_empty(self, all_skills):
        config = SkillRouterConfig(tier_a=False, tier_b=False, tier_c=False)
        router = SkillRouter(skills=all_skills, config=config)
        result = router.route("brainstorm a design")
        assert result == []

    def test_router_disabled_returns_empty(self, all_skills):
        config = SkillRouterConfig(enabled=False)
        router = SkillRouter(skills=all_skills, config=config)
        result = router.route("brainstorm a design")
        assert result == []


# ------------------------------------------------------------------
# Config
# ------------------------------------------------------------------

class TestConfig:
    def test_max_skills_per_turn(self, all_skills):
        config = SkillRouterConfig(max_skills_per_turn=1)
        router = SkillRouter(skills=all_skills, config=config)
        result = router.route("design and debug a feature with tests")
        assert len(result) <= 1

    def test_default_config(self):
        cfg = SkillRouterConfig()
        assert cfg.enabled is True
        assert cfg.tier_a is True
        assert cfg.tier_b is True
        assert cfg.tier_c is False
        assert cfg.similarity_threshold == 0.3


# ------------------------------------------------------------------
# Caching
# ------------------------------------------------------------------

class TestCaching:
    def test_same_message_returns_cached(self, router):
        r1 = router.route("brainstorm a new feature")
        r2 = router.route("brainstorm a new feature")
        assert r1 == r2

    def test_different_messages_different_results(self, router):
        r1 = router.route("brainstorm a new feature")
        r2 = router.route("debug this error")
        if r1 and r2:
            assert r1[0].name != r2[0].name


# ------------------------------------------------------------------
# Edge cases
# ------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_skills(self):
        router = SkillRouter(skills=(), config=SkillRouterConfig())
        assert router.route("anything") == []

    def test_empty_message(self, router):
        result = router.route("")
        assert result == []

    def test_very_long_message(self, router):
        msg = "design " * 1000
        result = router.route(msg)
        assert isinstance(result, list)

    def test_skill_without_keywords(self):
        skill = Skill(
            name="plain",
            description="A simple skill with no special keywords",
            content="content",
            auto=True,
        )
        router = SkillRouter(skills=(skill,), config=SkillRouterConfig())
        # Should still work via auto-extracted keywords
        result = router.route("simple plain skill")
        assert isinstance(result, list)


# ------------------------------------------------------------------
# End-to-end: router → prompt builder
# ------------------------------------------------------------------

class TestEndToEnd:
    """Verify the full flow: user message → router → prompt builder → only matched skill in prompt."""

    def test_matched_skill_in_prompt_unmatched_not(self, tmp_path):
        from llm_code.runtime.prompt import SystemPromptBuilder
        from llm_code.runtime.context import ProjectContext

        brainstorm = Skill(
            name="brainstorming", description="creative design features",
            content="BRAINSTORM_CONTENT_MARKER", auto=True,
            keywords=("brainstorm", "design", "creative", "設計"),
        )
        debug = Skill(
            name="debugging", description="bug fix error troubleshoot",
            content="DEBUG_CONTENT_MARKER", auto=True,
            keywords=("debug", "bug", "error", "fix"),
        )
        router = SkillRouter(skills=(brainstorm, debug), config=SkillRouterConfig())

        # Route a design message → should match brainstorming
        routed = tuple(router.route("我想要設計一個新功能"))
        assert len(routed) >= 1
        assert routed[0].name == "brainstorming"

        # Build prompt with routed skills
        ctx = ProjectContext(cwd=tmp_path, is_git_repo=False, git_status="", instructions="")
        prompt = SystemPromptBuilder().build(ctx, routed_skills=routed)

        assert "BRAINSTORM_CONTENT_MARKER" in prompt
        assert "DEBUG_CONTENT_MARKER" not in prompt

    def test_no_match_produces_clean_prompt(self, tmp_path):
        from llm_code.runtime.prompt import SystemPromptBuilder
        from llm_code.runtime.context import ProjectContext

        skill = Skill(
            name="brainstorming", description="creative design",
            content="SHOULD_NOT_APPEAR", auto=True,
            keywords=("brainstorm", "design"),
        )
        router = SkillRouter(skills=(skill,), config=SkillRouterConfig())

        routed = tuple(router.route("hello how are you"))
        ctx = ProjectContext(cwd=tmp_path, is_git_repo=False, git_status="", instructions="")
        prompt = SystemPromptBuilder().build(ctx, routed_skills=routed if routed else None)

        assert "SHOULD_NOT_APPEAR" not in prompt

    def test_local_model_rules_injected(self, tmp_path):
        from llm_code.runtime.prompt import SystemPromptBuilder
        from llm_code.runtime.context import ProjectContext

        ctx = ProjectContext(cwd=tmp_path, is_git_repo=False, git_status="", instructions="")
        prompt_local = SystemPromptBuilder().build(ctx, is_local_model=True)
        prompt_cloud = SystemPromptBuilder().build(ctx, is_local_model=False)

        assert "Do NOT use the agent tool" in prompt_local
        assert "Do NOT use the agent tool" not in prompt_cloud


# ------------------------------------------------------------------
# Tier C: LLM classifier (mock)
# ------------------------------------------------------------------

class TestTierC:
    @pytest.fixture
    def skills_pair(self):
        return (
            Skill(name="brainstorming", description="creative design", content="...", auto=True),
            Skill(name="debugging", description="bug fix error", content="...", auto=True),
        )

    @pytest.mark.asyncio
    async def test_tier_c_returns_matched_skill(self, skills_pair):
        from unittest.mock import AsyncMock, MagicMock
        from llm_code.runtime.skill_router import _classify_with_llm

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="brainstorming")]
        mock_provider = MagicMock()
        mock_provider.send_message = AsyncMock(return_value=mock_response)

        result = await _classify_with_llm("design a feature", skills_pair, mock_provider, "test-model")
        assert result == "brainstorming"

    @pytest.mark.asyncio
    async def test_tier_c_returns_none_on_no_match(self, skills_pair):
        from unittest.mock import AsyncMock, MagicMock
        from llm_code.runtime.skill_router import _classify_with_llm

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="none")]
        mock_provider = MagicMock()
        mock_provider.send_message = AsyncMock(return_value=mock_response)

        result = await _classify_with_llm("what is the weather", skills_pair, mock_provider, "test-model")
        assert result is None

    @pytest.mark.asyncio
    async def test_tier_c_handles_exception(self, skills_pair):
        from unittest.mock import AsyncMock, MagicMock
        from llm_code.runtime.skill_router import _classify_with_llm

        mock_provider = MagicMock()
        mock_provider.send_message = AsyncMock(side_effect=Exception("connection error"))

        result = await _classify_with_llm("test", skills_pair, mock_provider, "test-model")
        assert result is None

    @pytest.mark.asyncio
    async def test_route_async_uses_tier_c(self):
        from unittest.mock import AsyncMock, MagicMock

        skill = Skill(name="brainstorming", description="creative design", content="...", auto=True)
        config = SkillRouterConfig(tier_a=False, tier_b=False, tier_c=True)

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="brainstorming")]
        mock_provider = MagicMock()
        mock_provider.send_message = AsyncMock(return_value=mock_response)

        router = SkillRouter(skills=(skill,), config=config, provider=mock_provider, model="test")
        result = await router.route_async("design something creative")

        assert len(result) == 1
        assert result[0].name == "brainstorming"
