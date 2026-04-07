"""Tests for skill frontmatter hook registration."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from llm_code.runtime.frontmatter_hooks import (
    register_skill_hooks,
    register_skillset_hooks,
)
from llm_code.runtime.hooks import HookRunner
from llm_code.runtime.skills import Skill, SkillLoader


def _write_skill(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / "SKILL.md"
    p.write_text(body, encoding="utf-8")
    return p


class TestFrontmatterParsing:
    def test_hooks_parsed_from_frontmatter(self, tmp_path: Path) -> None:
        body = (
            "---\n"
            "name: demo\n"
            "description: d\n"
            "hooks:\n"
            "  pre_tool_use: auto_format\n"
            "  post_tool_use: auto_lint\n"
            "---\n"
            "content"
        )
        skill = SkillLoader.load_skill(_write_skill(tmp_path, "demo", body))
        assert len(skill.hooks) == 2
        assert ("pre_tool_use", "auto_format") in skill.hooks
        assert ("post_tool_use", "auto_lint") in skill.hooks

    def test_missing_hooks_section(self, tmp_path: Path) -> None:
        body = "---\nname: demo\ndescription: d\n---\ncontent"
        skill = SkillLoader.load_skill(_write_skill(tmp_path, "demo", body))
        assert skill.hooks == ()

    def test_non_string_handler_skipped(self, tmp_path: Path) -> None:
        body = (
            "---\n"
            "name: demo\n"
            "description: d\n"
            "hooks:\n"
            "  pre_tool_use: 123\n"
            "  post_tool_use: auto_lint\n"
            "---\n"
            "content"
        )
        skill = SkillLoader.load_skill(_write_skill(tmp_path, "demo", body))
        assert skill.hooks == (("post_tool_use", "auto_lint"),)


class TestRegisterSkillHooks:
    def test_known_handler_registers(self) -> None:
        skill = Skill(
            name="s1",
            description="d",
            content="",
            hooks=(("pre_tool_use", "auto_format"),),
        )
        runner = HookRunner()
        registered = register_skill_hooks(skill, runner)
        assert registered == ["pre_tool_use:auto_format"]
        # Subscription should exist on the runner
        assert any(runner._subscribers.values())  # noqa: SLF001 — test-only

    def test_unknown_handler_warns_and_skips(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        skill = Skill(
            name="s2",
            description="d",
            content="",
            hooks=(("pre_tool_use", "does_not_exist"),),
        )
        runner = HookRunner()
        with caplog.at_level(logging.WARNING):
            registered = register_skill_hooks(skill, runner)
        assert registered == []
        assert any("unknown hook" in r.message for r in caplog.records)

    def test_register_multiple_skills(self) -> None:
        skills = [
            Skill(
                name="s1",
                description="",
                content="",
                hooks=(("pre_tool_use", "auto_format"),),
            ),
            Skill(
                name="s2",
                description="",
                content="",
                hooks=(("post_tool_use", "auto_lint"),),
            ),
        ]
        runner = HookRunner()
        out = register_skillset_hooks(skills, runner)
        assert len(out) == 2
