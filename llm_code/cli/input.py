"""Input handler using prompt_toolkit for readline-like editing."""
from __future__ import annotations

from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory, InMemoryHistory


SLASH_COMMANDS = [
    "/help",
    "/clear",
    "/model",
    "/session",
    "/config",
    "/cd",
    "/image",
    "/cost",
    "/exit",
]


class InputHandler:
    """Handles user input with history, auto-suggest, and completion."""

    def __init__(self, history_path: Path | str | None = None) -> None:
        if history_path is not None:
            history: FileHistory | InMemoryHistory = FileHistory(str(history_path))
        else:
            history = InMemoryHistory()

        completer = WordCompleter(SLASH_COMMANDS, pattern=None)

        self._session: PromptSession = PromptSession(
            history=history,
            auto_suggest=AutoSuggestFromHistory(),
            completer=completer,
        )

    def read(self, prompt_text: str = "> ") -> str | None:
        """Read a line of input from the user.

        Returns None on Ctrl+C or Ctrl+D (EOFError/KeyboardInterrupt).
        """
        try:
            return self._session.prompt(prompt_text)
        except (EOFError, KeyboardInterrupt):
            return None
