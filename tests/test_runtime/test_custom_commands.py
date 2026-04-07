"""Tests for template-based custom slash commands."""
from __future__ import annotations

from pathlib import Path


from llm_code.runtime.custom_commands import (
    CustomCommand,
    discover_custom_commands,
)


class TestCustomCommandRender:
    def test_substitutes_arguments(self):
        cmd = CustomCommand(
            name="review",
            description="Code review",
            template="Review the diff for: $ARGUMENTS",
            source=Path("/tmp/review.md"),
        )
        assert cmd.render("PR #42") == "Review the diff for: PR #42"

    def test_empty_args_substitutes_none(self):
        cmd = CustomCommand(
            name="x",
            description="x",
            template="Args: $ARGUMENTS",
            source=Path("/tmp/x.md"),
        )
        assert cmd.render("") == "Args: (none)"

    def test_template_without_placeholder(self):
        cmd = CustomCommand(
            name="x",
            description="x",
            template="Just do something",
            source=Path("/tmp/x.md"),
        )
        assert cmd.render("ignored") == "Just do something"


class TestDiscoverCustomCommands:
    def test_discovers_project_command(self, tmp_path):
        cmds_dir = tmp_path / ".llmcode" / "commands"
        cmds_dir.mkdir(parents=True)
        (cmds_dir / "review.md").write_text(
            "---\n"
            "description: Run code review\n"
            "---\n"
            "Review the changes:\n$ARGUMENTS"
        )
        commands = discover_custom_commands(tmp_path)
        assert "review" in commands
        assert commands["review"].description == "Run code review"
        assert "Review the changes" in commands["review"].template

    def test_discovers_user_global_command(self, tmp_path, monkeypatch):
        # Use a fake home so we don't touch the real ~/.llmcode
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
        global_dir = tmp_path / "home" / ".llmcode" / "commands"
        global_dir.mkdir(parents=True)
        (global_dir / "myfix.md").write_text("Fix: $ARGUMENTS")
        commands = discover_custom_commands(tmp_path)
        assert "myfix" in commands

    def test_project_overrides_global(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
        global_dir = tmp_path / "home" / ".llmcode" / "commands"
        global_dir.mkdir(parents=True)
        (global_dir / "x.md").write_text("global version")

        project_dir = tmp_path / ".llmcode" / "commands"
        project_dir.mkdir(parents=True)
        (project_dir / "x.md").write_text("project version")

        commands = discover_custom_commands(tmp_path)
        assert "project version" in commands["x"].template
        assert "global" not in commands["x"].template

    def test_skips_invalid_filenames(self, tmp_path):
        cmds_dir = tmp_path / ".llmcode" / "commands"
        cmds_dir.mkdir(parents=True)
        # Invalid: starts with digit, contains uppercase, contains space
        (cmds_dir / "1bad.md").write_text("nope")
        (cmds_dir / "Bad.md").write_text("nope")
        (cmds_dir / "ok.md").write_text("yes")
        commands = discover_custom_commands(tmp_path)
        assert "ok" in commands
        assert "1bad" not in commands
        assert "bad" not in commands and "Bad" not in commands

    def test_skips_empty_files(self, tmp_path):
        cmds_dir = tmp_path / ".llmcode" / "commands"
        cmds_dir.mkdir(parents=True)
        (cmds_dir / "empty.md").write_text("")
        (cmds_dir / "frontmatter-only.md").write_text("---\ndescription: x\n---\n")
        commands = discover_custom_commands(tmp_path)
        assert "empty" not in commands
        assert "frontmatter-only" not in commands

    def test_no_frontmatter_uses_default_description(self, tmp_path):
        cmds_dir = tmp_path / ".llmcode" / "commands"
        cmds_dir.mkdir(parents=True)
        (cmds_dir / "raw.md").write_text("Just a template body")
        commands = discover_custom_commands(tmp_path)
        assert "raw" in commands
        assert "raw.md" in commands["raw"].description

    def test_returns_empty_when_no_directories(self, tmp_path):
        commands = discover_custom_commands(tmp_path)
        assert commands == {}

    def test_handles_corrupt_yaml(self, tmp_path):
        cmds_dir = tmp_path / ".llmcode" / "commands"
        cmds_dir.mkdir(parents=True)
        (cmds_dir / "bad.md").write_text("---\nthis is: not: valid: yaml\n---\nbody")
        commands = discover_custom_commands(tmp_path)
        # Should still load (just empty description)
        assert "bad" in commands
