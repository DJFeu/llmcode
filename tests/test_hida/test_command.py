"""Tests for /hida slash command."""
from __future__ import annotations

from llm_code.cli.commands import KNOWN_COMMANDS, parse_slash_command


class TestHidaCommand:
    def test_hida_in_known_commands(self):
        assert "hida" in KNOWN_COMMANDS

    def test_parse_hida_command(self):
        result = parse_slash_command("/hida")
        assert result is not None
        assert result.name == "hida"
        assert result.args == ""

    def test_parse_hida_with_args(self):
        result = parse_slash_command("/hida status")
        assert result is not None
        assert result.name == "hida"
        assert result.args == "status"
