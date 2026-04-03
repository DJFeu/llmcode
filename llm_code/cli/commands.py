"""Slash command parsing for the CLI layer."""
from __future__ import annotations

from dataclasses import dataclass

KNOWN_COMMANDS = frozenset({
    "help",
    "clear",
    "model",
    "session",
    "config",
    "cd",
    "image",
    "cost",
    "exit",
    "quit",
    "plugin",
    "skill",
    "undo",
    "memory",
    "index",
    "lsp",
    "mcp",
    "budget",
    "thinking",
    "cron",
    "vim",
    "voice",
    "ide",
    "swarm",
    "search",
    "vcr",
    "hida",
    "task",
    "checkpoint",
})


@dataclass(frozen=True)
class SlashCommand:
    name: str
    args: str


def parse_slash_command(text: str) -> SlashCommand | None:
    """Parse a slash command from text.

    Returns a SlashCommand if the text starts with '/', otherwise None.
    """
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None

    # Strip leading slash
    rest = stripped[1:]

    # Split on first whitespace
    parts = rest.split(None, 1)
    if not parts:
        return None

    name = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    return SlashCommand(name=name, args=args)
