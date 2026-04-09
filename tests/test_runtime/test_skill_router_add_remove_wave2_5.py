"""Wave2-5: SkillRouter.add_skill / remove_skill round-trip.

The plugin executor needs a way to feed plugin-provided skills into
a live SkillRouter without rebuilding it from scratch. These tests
pin the basic add/remove contract plus index invalidation.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from llm_code.runtime.config import SkillRouterConfig
from llm_code.runtime.skill_router import SkillRouter


@dataclass
class _FakeSkill:
    name: str
    description: str = ""
    content: str = ""
    keywords: frozenset[str] = field(default_factory=frozenset)


def _make_router(*skills: _FakeSkill) -> SkillRouter:
    return SkillRouter(
        skills=list(skills),
        config=SkillRouterConfig(enabled=True, tier_b=False),
    )


def test_add_skill_grows_skill_list() -> None:
    router = _make_router(_FakeSkill(name="alpha"))
    assert len(router._skills) == 1
    router.add_skill(_FakeSkill(name="beta"))
    assert len(router._skills) == 2
    assert any(s.name == "beta" for s in router._skills)


def test_add_skill_rejects_duplicate_name() -> None:
    import pytest
    router = _make_router(_FakeSkill(name="alpha"))
    with pytest.raises(ValueError, match="already registered"):
        router.add_skill(_FakeSkill(name="alpha"))


def test_add_skill_invalidates_cache() -> None:
    """A cached 'no match' result must be invalidated so a later
    add_skill call doesn't get shadowed by the stale cache."""
    router = _make_router()
    router._cache["some key"] = []  # simulate a prior cache entry
    router.add_skill(_FakeSkill(name="new"))
    assert router._cache == {}


def test_remove_skill_returns_false_for_unknown() -> None:
    router = _make_router(_FakeSkill(name="alpha"))
    assert router.remove_skill("never-existed") is False
    assert len(router._skills) == 1


def test_remove_skill_actually_removes() -> None:
    router = _make_router(
        _FakeSkill(name="alpha"),
        _FakeSkill(name="beta"),
    )
    assert router.remove_skill("alpha") is True
    assert [s.name for s in router._skills] == ["beta"]


def test_remove_skill_invalidates_cache() -> None:
    router = _make_router(_FakeSkill(name="alpha"))
    router._cache["some key"] = ["stale"]
    router.remove_skill("alpha")
    assert router._cache == {}


def test_add_then_remove_is_idempotent() -> None:
    router = _make_router(_FakeSkill(name="base"))
    router.add_skill(_FakeSkill(name="plugin-skill"))
    router.remove_skill("plugin-skill")
    assert [s.name for s in router._skills] == ["base"]
    # Second remove is harmless
    assert router.remove_skill("plugin-skill") is False
