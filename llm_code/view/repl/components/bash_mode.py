"""Bash mode input state + dispatcher routing helper (M15 Task B5).

When the user's buffer starts with ``!``, the REPL enters "bash
mode": the footer's mode indicator flips to ``[bash]`` and the
remaining text is dispatched to the bash tool instead of the LLM.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["BashModeState", "is_bash_mode_input", "strip_bash_prefix"]


@dataclass
class BashModeState:
    """Tracks whether the active buffer is in bash mode."""

    active: bool = False

    def set_from_buffer(self, text: str) -> None:
        self.active = text.startswith("!")


def is_bash_mode_input(text: str) -> bool:
    """Return True when the submitted text should route to the bash tool."""
    return text.startswith("!")


def strip_bash_prefix(text: str) -> str:
    """Remove the leading ``!`` from a bash mode command."""
    return text[1:] if text.startswith("!") else text
