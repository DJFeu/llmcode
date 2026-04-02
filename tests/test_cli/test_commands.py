"""Tests for CLI slash command parsing."""
from __future__ import annotations


from llm_code.cli.commands import SlashCommand, parse_slash_command


class TestParseSlashCommand:
    def test_help(self):
        cmd = parse_slash_command("/help")
        assert cmd == SlashCommand(name="help", args="")

    def test_clear(self):
        cmd = parse_slash_command("/clear")
        assert cmd == SlashCommand(name="clear", args="")

    def test_model_with_arg(self):
        cmd = parse_slash_command("/model qwen3:32b")
        assert cmd == SlashCommand(name="model", args="qwen3:32b")

    def test_session_subcommand(self):
        cmd = parse_slash_command("/session list")
        assert cmd == SlashCommand(name="session", args="list")

    def test_session_switch(self):
        cmd = parse_slash_command("/session switch abc123")
        assert cmd == SlashCommand(name="session", args="switch abc123")

    def test_config_set(self):
        cmd = parse_slash_command("/config set temperature 0.5")
        assert cmd == SlashCommand(name="config", args="set temperature 0.5")

    def test_cd(self):
        cmd = parse_slash_command("/cd /tmp")
        assert cmd == SlashCommand(name="cd", args="/tmp")

    def test_image(self):
        cmd = parse_slash_command("/image /path/to/screenshot.png")
        assert cmd == SlashCommand(name="image", args="/path/to/screenshot.png")

    def test_exit(self):
        cmd = parse_slash_command("/exit")
        assert cmd == SlashCommand(name="exit", args="")

    def test_cost(self):
        cmd = parse_slash_command("/cost")
        assert cmd == SlashCommand(name="cost", args="")

    def test_not_a_command(self):
        result = parse_slash_command("explain this code")
        assert result is None

    def test_empty_string(self):
        result = parse_slash_command("")
        assert result is None

    def test_whitespace_only(self):
        result = parse_slash_command("   ")
        assert result is None

    def test_leading_whitespace(self):
        cmd = parse_slash_command("  /help  ")
        assert cmd == SlashCommand(name="help", args="")

    def test_command_name_lowercased(self):
        cmd = parse_slash_command("/HELP")
        assert cmd is not None
        assert cmd.name == "help"

    def test_model_no_arg(self):
        cmd = parse_slash_command("/model")
        assert cmd == SlashCommand(name="model", args="")

    def test_slash_only(self):
        result = parse_slash_command("/")
        assert result is None
