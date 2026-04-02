"""Tests for the Skills system (SkillLoader, SkillSet, Skill)."""
from __future__ import annotations

from pathlib import Path

import pytest

from llm_code.runtime.skills import Skill, SkillLoader


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_skill_dir(base: Path, name: str, *, content: str) -> Path:
    """Create a skill directory with a SKILL.md file."""
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    return skill_dir


_AUTO_SKILL_MD = """\
---
name: linter
description: Auto-lint code
auto: true
trigger: linter
---

Run the linter on all Python files.
"""

_COMMAND_SKILL_MD = """\
---
name: code-review
description: Review code for quality
auto: false
trigger: review
---

Please review the following code.
"""

_MINIMAL_SKILL_MD = """\
---
name: minimal
description: Minimal skill
---

Minimal content.
"""


# ---------------------------------------------------------------------------
# Skill dataclass
# ---------------------------------------------------------------------------

class TestSkill:
    def test_skill_fields(self) -> None:
        s = Skill(
            name="foo",
            description="bar",
            content="baz",
            auto=False,
            trigger="foo",
        )
        assert s.name == "foo"
        assert s.description == "bar"
        assert s.content == "baz"
        assert s.auto is False
        assert s.trigger == "foo"

    def test_skill_is_frozen(self) -> None:
        s = Skill(name="x", description="d", content="c", auto=False, trigger="x")
        with pytest.raises((AttributeError, TypeError)):
            s.name = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# SkillLoader.load_skill
# ---------------------------------------------------------------------------

class TestLoadSkill:
    def test_load_auto_skill(self, tmp_path: Path) -> None:
        skill_dir = _make_skill_dir(tmp_path, "linter", content=_AUTO_SKILL_MD)
        skill = SkillLoader.load_skill(skill_dir / "SKILL.md")
        assert skill.name == "linter"
        assert skill.description == "Auto-lint code"
        assert skill.auto is True
        assert skill.trigger == "linter"
        assert "Run the linter" in skill.content

    def test_load_command_skill(self, tmp_path: Path) -> None:
        skill_dir = _make_skill_dir(tmp_path, "code-review", content=_COMMAND_SKILL_MD)
        skill = SkillLoader.load_skill(skill_dir / "SKILL.md")
        assert skill.name == "code-review"
        assert skill.auto is False
        assert skill.trigger == "review"
        assert "review the following" in skill.content

    def test_default_auto_is_false(self, tmp_path: Path) -> None:
        skill_dir = _make_skill_dir(tmp_path, "minimal", content=_MINIMAL_SKILL_MD)
        skill = SkillLoader.load_skill(skill_dir / "SKILL.md")
        assert skill.auto is False

    def test_default_trigger_is_name(self, tmp_path: Path) -> None:
        skill_dir = _make_skill_dir(tmp_path, "minimal", content=_MINIMAL_SKILL_MD)
        skill = SkillLoader.load_skill(skill_dir / "SKILL.md")
        assert skill.trigger == skill.name

    def test_content_extracted(self, tmp_path: Path) -> None:
        md = "---\nname: t\ndescription: d\n---\n\nHello world.\n"
        skill_dir = _make_skill_dir(tmp_path, "t", content=md)
        skill = SkillLoader.load_skill(skill_dir / "SKILL.md")
        assert "Hello world." in skill.content


# ---------------------------------------------------------------------------
# SkillLoader.load_from_dirs
# ---------------------------------------------------------------------------

class TestLoadFromDirs:
    def test_separates_auto_vs_command(self, tmp_path: Path) -> None:
        _make_skill_dir(tmp_path, "linter", content=_AUTO_SKILL_MD)
        _make_skill_dir(tmp_path, "code-review", content=_COMMAND_SKILL_MD)

        skill_set = SkillLoader.load_from_dirs([tmp_path])
        auto_names = {s.name for s in skill_set.auto_skills}
        cmd_names = {s.name for s in skill_set.command_skills}

        assert "linter" in auto_names
        assert "code-review" in cmd_names
        assert "code-review" not in auto_names
        assert "linter" not in cmd_names

    def test_load_from_multiple_dirs(self, tmp_path: Path) -> None:
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        _make_skill_dir(dir_a, "linter", content=_AUTO_SKILL_MD)
        _make_skill_dir(dir_b, "code-review", content=_COMMAND_SKILL_MD)

        skill_set = SkillLoader.load_from_dirs([dir_a, dir_b])
        assert len(skill_set.auto_skills) == 1
        assert len(skill_set.command_skills) == 1

    def test_empty_dir_returns_empty_skillset(self, tmp_path: Path) -> None:
        skill_set = SkillLoader.load_from_dirs([tmp_path])
        assert skill_set.auto_skills == ()
        assert skill_set.command_skills == ()

    def test_nonexistent_dir_is_skipped(self, tmp_path: Path) -> None:
        nonexistent = tmp_path / "does_not_exist"
        skill_set = SkillLoader.load_from_dirs([nonexistent])
        assert skill_set.auto_skills == ()
        assert skill_set.command_skills == ()

    def test_dir_without_skill_md_skipped(self, tmp_path: Path) -> None:
        # A subdirectory without SKILL.md should be ignored
        (tmp_path / "not-a-skill").mkdir()
        skill_set = SkillLoader.load_from_dirs([tmp_path])
        assert skill_set.auto_skills == ()
        assert skill_set.command_skills == ()

    def test_skillset_is_frozen(self, tmp_path: Path) -> None:
        skill_set = SkillLoader.load_from_dirs([tmp_path])
        with pytest.raises((AttributeError, TypeError)):
            skill_set.auto_skills = ()  # type: ignore[misc]
