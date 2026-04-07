"""Tests for SkillLoadTool."""
from __future__ import annotations

import pytest

from llm_code.runtime.skills import Skill, SkillSet
from llm_code.tools.skill_load import SkillLoadTool


@pytest.fixture
def skills() -> SkillSet:
    return SkillSet(
        auto_skills=(
            Skill(
                name="brainstorming",
                description="Use when planning new features",
                content="Brainstorming workflow content here.",
                auto=True,
            ),
        ),
        command_skills=(
            Skill(
                name="tdd",
                description="Test-driven development workflow",
                content="TDD steps: red, green, refactor.",
                auto=False,
            ),
        ),
    )


class TestSkillLoadTool:
    def test_name(self, skills):
        assert SkillLoadTool(skills).name == "skill_load"

    def test_description_lists_all_skills(self, skills):
        desc = SkillLoadTool(skills).description
        assert "brainstorming" in desc
        assert "tdd" in desc
        assert "Use when planning new features" in desc

    def test_description_when_no_skills(self):
        empty = SkillSet(auto_skills=(), command_skills=())
        desc = SkillLoadTool(empty).description
        assert "No skills" in desc

    def test_description_when_skills_is_none(self):
        desc = SkillLoadTool(None).description
        assert "No skills" in desc

    def test_load_existing_auto_skill(self, skills):
        result = SkillLoadTool(skills).execute({"name": "brainstorming"})
        assert result.is_error is False
        assert "Brainstorming workflow" in result.output
        assert '<skill_content name="brainstorming">' in result.output
        assert result.metadata["skill_name"] == "brainstorming"

    def test_load_existing_command_skill(self, skills):
        result = SkillLoadTool(skills).execute({"name": "tdd"})
        assert result.is_error is False
        assert "red, green, refactor" in result.output

    def test_load_nonexistent_skill(self, skills):
        result = SkillLoadTool(skills).execute({"name": "nonexistent"})
        assert result.is_error is True
        assert "not found" in result.output
        # Lists available skills
        assert "brainstorming" in result.output
        assert "tdd" in result.output

    def test_load_with_no_skills_set(self):
        result = SkillLoadTool(None).execute({"name": "anything"})
        assert result.is_error is True

    def test_is_read_only(self, skills):
        assert SkillLoadTool(skills).is_read_only({}) is True
        assert SkillLoadTool(skills).is_concurrency_safe({}) is True

    def test_input_schema_requires_name(self, skills):
        schema = SkillLoadTool(skills).input_schema
        assert "name" in schema["required"]
        assert schema["properties"]["name"]["type"] == "string"

    def test_to_definition(self, skills):
        defn = SkillLoadTool(skills).to_definition()
        assert defn.name == "skill_load"
        assert "name" in defn.input_schema["properties"]
