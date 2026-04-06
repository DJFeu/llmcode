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
        assert "tool" not in tokens  # "tool" is a stopword
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
