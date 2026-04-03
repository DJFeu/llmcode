"""Tests for /ide slash command parsing and integration."""
from __future__ import annotations

from llm_code.cli.commands import KNOWN_COMMANDS, parse_slash_command


class TestIDESlashCommand:
    def test_ide_in_known_commands(self):
        assert "ide" in KNOWN_COMMANDS

    def test_parse_ide_status(self):
        cmd = parse_slash_command("/ide status")
        assert cmd is not None
        assert cmd.name == "ide"
        assert cmd.args == "status"

    def test_parse_ide_connect(self):
        cmd = parse_slash_command("/ide connect")
        assert cmd is not None
        assert cmd.name == "ide"
        assert cmd.args == "connect"

    def test_parse_ide_no_args(self):
        cmd = parse_slash_command("/ide")
        assert cmd is not None
        assert cmd.name == "ide"
        assert cmd.args == ""
