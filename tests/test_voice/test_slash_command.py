"""Tests for /voice slash command."""
from __future__ import annotations

from llm_code.cli.commands import parse_slash_command, KNOWN_COMMANDS


class TestVoiceSlashCommand:
    def test_voice_in_known_commands(self):
        assert "voice" in KNOWN_COMMANDS

    def test_parse_voice_on(self):
        cmd = parse_slash_command("/voice on")
        assert cmd is not None
        assert cmd.name == "voice"
        assert cmd.args == "on"

    def test_parse_voice_off(self):
        cmd = parse_slash_command("/voice off")
        assert cmd is not None
        assert cmd.name == "voice"
        assert cmd.args == "off"

    def test_parse_voice_no_args(self):
        cmd = parse_slash_command("/voice")
        assert cmd is not None
        assert cmd.name == "voice"
        assert cmd.args == ""
