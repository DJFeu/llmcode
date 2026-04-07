"""Tests for built-in slash commands ported from oh-my-opencode."""
from __future__ import annotations

from pathlib import Path

from llm_code.runtime.custom_commands import (
    _BUILTIN_COMMANDS_DIR,
    discover_custom_commands,
)

EXPECTED_BUILTIN = {"init-deep", "ralph-loop", "refactor", "start-work"}


class TestBuiltinCommandsDirectory:
    def test_directory_exists(self):
        assert _BUILTIN_COMMANDS_DIR.is_dir()

    def test_all_expected_files_present(self):
        files = {p.stem for p in _BUILTIN_COMMANDS_DIR.glob("*.md")}
        assert EXPECTED_BUILTIN.issubset(files)


class TestBuiltinCommandsDiscovery:
    def test_builtin_commands_discoverable_with_no_user_dirs(self, tmp_path, monkeypatch):
        # Use a clean fake home so user-global commands don't interfere
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
        commands = discover_custom_commands(tmp_path)
        for name in EXPECTED_BUILTIN:
            assert name in commands, f"Missing built-in command: {name}"

    def test_builtin_commands_have_template_with_arguments_placeholder(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
        commands = discover_custom_commands(tmp_path)
        for name in EXPECTED_BUILTIN:
            cmd = commands[name]
            assert "$ARGUMENTS" in cmd.template, f"{name} missing $ARGUMENTS placeholder"

    def test_builtin_commands_render_with_args(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
        commands = discover_custom_commands(tmp_path)
        rendered = commands["refactor"].render("auth.py --scope=file")
        assert "auth.py --scope=file" in rendered

    def test_builtin_commands_have_descriptions(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
        commands = discover_custom_commands(tmp_path)
        for name in EXPECTED_BUILTIN:
            assert commands[name].description.strip() != ""

    def test_user_command_overrides_builtin(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
        user_dir = tmp_path / "home" / ".llmcode" / "commands"
        user_dir.mkdir(parents=True)
        (user_dir / "refactor.md").write_text(
            "---\ndescription: My override\n---\nMy template $ARGUMENTS"
        )
        commands = discover_custom_commands(tmp_path)
        assert commands["refactor"].description == "My override"
        assert "My template" in commands["refactor"].template

    def test_project_command_overrides_builtin(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
        project_dir = tmp_path / ".llmcode" / "commands"
        project_dir.mkdir(parents=True)
        (project_dir / "init-deep.md").write_text(
            "---\ndescription: Project init\n---\nProject body $ARGUMENTS"
        )
        commands = discover_custom_commands(tmp_path)
        assert commands["init-deep"].description == "Project init"
        assert "Project body" in commands["init-deep"].template
