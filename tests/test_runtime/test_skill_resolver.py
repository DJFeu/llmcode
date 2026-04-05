"""Tests for SkillResolver — dependency resolution for skills."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from llm_code.runtime.skill_resolver import SkillResolver
from llm_code.runtime.skills import Skill, SkillDependency


def _make_skill(name: str, depends: tuple[SkillDependency, ...] = (), min_version: str = "") -> Skill:
    return Skill(name=name, description="d", content="c", depends=depends, min_version=min_version)


class TestSkillResolverCheckInstalled:
    def test_no_deps_returns_empty(self) -> None:
        resolver = SkillResolver(installed_skills={"a"}, installer=MagicMock())
        missing = resolver.find_missing(_make_skill("a"))
        assert missing == []

    def test_all_deps_installed(self) -> None:
        skill = _make_skill("a", depends=(SkillDependency(name="b"),))
        resolver = SkillResolver(installed_skills={"a", "b"}, installer=MagicMock())
        assert resolver.find_missing(skill) == []

    def test_missing_dep_returned(self) -> None:
        skill = _make_skill("a", depends=(SkillDependency(name="b"),))
        resolver = SkillResolver(installed_skills={"a"}, installer=MagicMock())
        missing = resolver.find_missing(skill)
        assert len(missing) == 1
        assert missing[0].name == "b"

    def test_multiple_missing(self) -> None:
        skill = _make_skill("a", depends=(
            SkillDependency(name="b"),
            SkillDependency(name="c"),
        ))
        resolver = SkillResolver(installed_skills={"a"}, installer=MagicMock())
        missing = resolver.find_missing(skill)
        assert {d.name for d in missing} == {"b", "c"}


class TestSkillResolverCycleDetection:
    def test_cycle_raises(self) -> None:
        resolver = SkillResolver(installed_skills=set(), installer=MagicMock())
        with pytest.raises(ValueError, match="[Cc]ircular"):
            resolver._check_cycle("a", frozenset({"a"}))

    def test_no_cycle(self) -> None:
        resolver = SkillResolver(installed_skills=set(), installer=MagicMock())
        resolver._check_cycle("a", frozenset({"b", "c"}))


class TestSkillResolverMaxDepth:
    def test_exceeds_max_depth_raises(self) -> None:
        resolver = SkillResolver(installed_skills=set(), installer=MagicMock(), max_depth=3)
        with pytest.raises(ValueError, match="[Dd]epth"):
            resolver._check_depth(4)

    def test_within_max_depth_ok(self) -> None:
        resolver = SkillResolver(installed_skills=set(), installer=MagicMock(), max_depth=3)
        resolver._check_depth(3)


class TestSkillResolverMinVersion:
    @pytest.fixture(autouse=True)
    def _patch_version(self, monkeypatch):
        """Default to version 1.0.0 unless overridden."""
        monkeypatch.setattr("llm_code.runtime.skill_resolver._get_llm_code_version", lambda: "1.0.0")

    def test_compatible_version(self) -> None:
        resolver = SkillResolver(installed_skills=set(), installer=MagicMock())
        warnings = resolver.check_min_version(_make_skill("a", min_version="0.8.0"))
        assert warnings == []

    def test_incompatible_version_warns(self, monkeypatch) -> None:
        monkeypatch.setattr("llm_code.runtime.skill_resolver._get_llm_code_version", lambda: "0.5.0")
        resolver = SkillResolver(installed_skills=set(), installer=MagicMock())
        warnings = resolver.check_min_version(_make_skill("a", min_version="0.8.0"))
        assert len(warnings) == 1
        assert "0.8.0" in warnings[0]

    def test_no_min_version_no_warning(self) -> None:
        resolver = SkillResolver(installed_skills=set(), installer=MagicMock())
        warnings = resolver.check_min_version(_make_skill("a"))
        assert warnings == []
