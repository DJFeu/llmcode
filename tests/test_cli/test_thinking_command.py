"""Tests for /thinking slash command."""
from __future__ import annotations

from llm_code.cli.commands import SlashCommand, parse_slash_command


class TestThinkingCommand:
    def test_thinking_no_args(self):
        cmd = parse_slash_command("/thinking")
        assert cmd == SlashCommand(name="thinking", args="")

    def test_thinking_adaptive(self):
        cmd = parse_slash_command("/thinking adaptive")
        assert cmd == SlashCommand(name="thinking", args="adaptive")

    def test_thinking_on(self):
        cmd = parse_slash_command("/thinking on")
        assert cmd == SlashCommand(name="thinking", args="on")

    def test_thinking_off(self):
        cmd = parse_slash_command("/thinking off")
        assert cmd == SlashCommand(name="thinking", args="off")

    def test_thinking_is_known_command(self):
        from llm_code.cli.commands import KNOWN_COMMANDS
        assert "thinking" in KNOWN_COMMANDS
