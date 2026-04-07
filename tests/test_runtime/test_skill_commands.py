"""Tests for skill-declared dynamic slash commands."""
from __future__ import annotations

from pathlib import Path

from llm_code.cli.commands import (
    COMMAND_REGISTRY,
    CommandDef,
    KNOWN_COMMANDS,
    all_known_commands,
    register_skill_commands,
)
from llm_code.runtime.skills import Skill, SkillLoader


def test_frontmatter_commands_parsed(tmp_path: Path) -> None:
    body = (
        "---\n"
        "name: reviewer\n"
        "description: d\n"
        "commands:\n"
        "  - name: review\n"
        "    description: Review a file\n"
        "    argument_hint: <path>\n"
        "  - name: quick\n"
        "    description: Quick pass\n"
        "---\n"
        "content"
    )
    p = tmp_path / "SKILL.md"
    p.write_text(body, encoding="utf-8")
    skill = SkillLoader.load_skill(p)
    assert len(skill.commands) == 2
    assert skill.commands[0]["name"] == "review"
    assert skill.commands[0]["argument_hint"] == "<path>"
    assert skill.commands[1]["name"] == "quick"


def test_register_adds_to_target() -> None:
    skill = Skill(
        name="myskill",
        description="",
        content="",
        commands=(
            {"name": "frob", "description": "Frob it", "argument_hint": "<x>"},
        ),
    )
    registry: dict[str, CommandDef] = {}
    out = register_skill_commands(skill, registry)
    assert out == ["frob"]
    assert "frob" in registry
    assert registry["frob"].description == "Frob it"
    assert registry["frob"].no_arg is False  # has hint


def test_collision_prefixed_with_skill_name() -> None:
    # "help" is in KNOWN_COMMANDS, so it should be prefixed.
    assert "help" in KNOWN_COMMANDS
    skill = Skill(
        name="myskill",
        description="",
        content="",
        commands=({"name": "help", "description": "My help"},),
    )
    registry: dict[str, CommandDef] = {}
    out = register_skill_commands(skill, registry)
    assert out == ["myskill/help"]
    assert "myskill/help" in registry


def test_no_arg_inferred_from_missing_hint() -> None:
    skill = Skill(
        name="s",
        description="",
        content="",
        commands=({"name": "ping", "description": "pong"},),
    )
    registry: dict[str, CommandDef] = {}
    register_skill_commands(skill, registry)
    assert registry["ping"].no_arg is True


def test_empty_name_skipped() -> None:
    skill = Skill(
        name="s",
        description="",
        content="",
        commands=({"name": "", "description": "x"},),
    )
    registry: dict[str, CommandDef] = {}
    out = register_skill_commands(skill, registry)
    assert out == []


def test_all_known_commands_includes_skills() -> None:
    skill = Skill(
        name="s99",
        description="",
        content="",
        commands=({"name": "unique_test_cmd_xyz", "description": "x"},),
    )
    registered = register_skill_commands(skill)
    try:
        assert "unique_test_cmd_xyz" in all_known_commands()
        assert "unique_test_cmd_xyz" not in all_known_commands(include_skills=False)
    finally:
        from llm_code.cli.commands import SKILL_COMMANDS
        for name in registered:
            SKILL_COMMANDS.pop(name, None)


def test_static_registry_unchanged() -> None:
    # sanity: we didn't break the canonical tuple.
    assert any(c.name == "help" for c in COMMAND_REGISTRY)
