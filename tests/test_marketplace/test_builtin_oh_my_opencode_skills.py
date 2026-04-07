"""Tests for oh-my-opencode built-in skills shipped with llm-code."""
from __future__ import annotations

from pathlib import Path

from llm_code.runtime.skills import SkillLoader

import llm_code.marketplace as _mkt_pkg

BUILTIN_ROOT = Path(_mkt_pkg.__file__).parent / "builtin" / "oh-my-opencode-skills"
SKILLS_DIR = BUILTIN_ROOT / "skills"

EXPECTED_SKILLS = {"playwright", "agent-browser", "frontend-ui-ux", "git-master"}


class TestOhMyOpencodeSkills:
    def test_plugin_manifest_exists(self):
        manifest = BUILTIN_ROOT / ".claude-plugin" / "plugin.json"
        assert manifest.is_file()

    def test_skills_directory_exists(self):
        assert SKILLS_DIR.is_dir()

    def test_all_expected_skills_present(self):
        found = {
            sub.name
            for sub in SKILLS_DIR.iterdir()
            if sub.is_dir() and (sub / "SKILL.md").is_file()
        }
        assert EXPECTED_SKILLS.issubset(found)

    def test_skills_load_via_skill_loader(self):
        skill_set = SkillLoader().load_from_dirs([SKILLS_DIR])
        all_names = {s.name for s in skill_set.command_skills} | {
            s.name for s in skill_set.auto_skills
        }
        assert EXPECTED_SKILLS.issubset(all_names)

    def test_loaded_skills_have_descriptions(self):
        skill_set = SkillLoader().load_from_dirs([SKILLS_DIR])
        for skill in skill_set.command_skills + skill_set.auto_skills:
            if skill.name in EXPECTED_SKILLS:
                assert skill.description.strip() != ""
                assert len(skill.content.strip()) > 100

    def test_git_master_mentions_atomic_commits(self):
        skill_set = SkillLoader().load_from_dirs([SKILLS_DIR])
        git = next(s for s in skill_set.command_skills if s.name == "git-master")
        assert "atomic" in git.content.lower() or "multiple commits" in git.content.lower()

    def test_playwright_mentions_mcp(self):
        skill_set = SkillLoader().load_from_dirs([SKILLS_DIR])
        pw = next(
            s
            for s in (*skill_set.command_skills, *skill_set.auto_skills)
            if s.name == "playwright"
        )
        assert "mcp" in pw.content.lower()
