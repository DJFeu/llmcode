"""Tests for the Skills system (SkillLoader, SkillSet, Skill)."""
from __future__ import annotations

from pathlib import Path

import pytest

from llm_code.runtime.skills import Skill, SkillDependency, SkillLoader


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


# ---------------------------------------------------------------------------
# SkillDependency dataclass
# ---------------------------------------------------------------------------

class TestSkillDependency:
    def test_create_with_name_only(self) -> None:
        dep = SkillDependency(name="base-tools")
        assert dep.name == "base-tools"
        assert dep.registry == ""

    def test_create_with_registry(self) -> None:
        dep = SkillDependency(name="base-tools", registry="official")
        assert dep.registry == "official"

    def test_frozen(self) -> None:
        dep = SkillDependency(name="x")
        with pytest.raises(AttributeError):
            dep.name = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Extended Skill fields
# ---------------------------------------------------------------------------

class TestSkillExtendedFields:
    def test_default_new_fields(self) -> None:
        skill = Skill(name="test", description="desc", content="body")
        assert skill.version == ""
        assert skill.tags == ()
        assert skill.model == ""
        assert skill.depends == ()
        assert skill.min_version == ""

    def test_new_fields_populated(self) -> None:
        skill = Skill(
            name="test",
            description="desc",
            content="body",
            version="1.2.0",
            tags=("debug", "python"),
            model="sonnet",
            depends=(SkillDependency(name="base"),),
            min_version="0.8.0",
        )
        assert skill.version == "1.2.0"
        assert skill.tags == ("debug", "python")
        assert skill.model == "sonnet"
        assert len(skill.depends) == 1
        assert skill.depends[0].name == "base"
        assert skill.min_version == "0.8.0"

    def test_existing_fields_unchanged(self) -> None:
        skill = Skill(name="x", description="d", content="c", auto=True, trigger="go")
        assert skill.auto is True
        assert skill.trigger == "go"


# ---------------------------------------------------------------------------
# SkillLoader extended frontmatter parsing (YAML)
# ---------------------------------------------------------------------------

class TestSkillLoaderExtendedFrontmatter:
    def test_parse_version(self, tmp_path) -> None:
        p = tmp_path / "SKILL.md"
        p.write_text("---\nname: demo\ndescription: d\nversion: 2.0.1\n---\nbody\n")
        skill = SkillLoader.load_skill(p)
        assert skill.version == "2.0.1"

    def test_parse_tags(self, tmp_path) -> None:
        p = tmp_path / "SKILL.md"
        p.write_text("---\nname: demo\ndescription: d\ntags: [debug, python]\n---\nbody\n")
        skill = SkillLoader.load_skill(p)
        assert skill.tags == ("debug", "python")

    def test_parse_model(self, tmp_path) -> None:
        p = tmp_path / "SKILL.md"
        p.write_text("---\nname: demo\ndescription: d\nmodel: haiku\n---\nbody\n")
        skill = SkillLoader.load_skill(p)
        assert skill.model == "haiku"

    def test_parse_min_version(self, tmp_path) -> None:
        p = tmp_path / "SKILL.md"
        p.write_text("---\nname: demo\ndescription: d\nmin_version: 0.8.0\n---\nbody\n")
        skill = SkillLoader.load_skill(p)
        assert skill.min_version == "0.8.0"

    def test_parse_depends_single(self, tmp_path) -> None:
        p = tmp_path / "SKILL.md"
        content = "---\nname: demo\ndescription: d\ndepends:\n  - name: base-tools\n---\nbody\n"
        p.write_text(content)
        skill = SkillLoader.load_skill(p)
        assert len(skill.depends) == 1
        assert skill.depends[0].name == "base-tools"
        assert skill.depends[0].registry == ""

    def test_parse_depends_with_registry(self, tmp_path) -> None:
        p = tmp_path / "SKILL.md"
        content = "---\nname: demo\ndescription: d\ndepends:\n  - name: base-tools\n    registry: official\n---\nbody\n"
        p.write_text(content)
        skill = SkillLoader.load_skill(p)
        assert skill.depends[0].registry == "official"

    def test_parse_depends_multiple(self, tmp_path) -> None:
        p = tmp_path / "SKILL.md"
        content = "---\nname: demo\ndescription: d\ndepends:\n  - name: a\n  - name: b\n---\nbody\n"
        p.write_text(content)
        skill = SkillLoader.load_skill(p)
        assert len(skill.depends) == 2

    def test_no_new_fields_gives_defaults(self, tmp_path) -> None:
        p = tmp_path / "SKILL.md"
        p.write_text("---\nname: demo\ndescription: d\n---\nbody\n")
        skill = SkillLoader.load_skill(p)
        assert skill.version == ""
        assert skill.tags == ()
        assert skill.model == ""
        assert skill.depends == ()
        assert skill.min_version == ""
