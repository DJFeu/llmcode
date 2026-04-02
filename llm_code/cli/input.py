"""Input handler using prompt_toolkit for readline-like editing."""
from __future__ import annotations

import sys
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.keys import Keys


SLASH_COMMANDS = [
    "/help",
    "/clear",
    "/model",
    "/session list",
    "/session save",
    "/session switch",
    "/config set",
    "/cd",
    "/image",
    "/cost",
    "/plugin",
    "/plugin search",
    "/plugin install",
    "/plugin enable",
    "/plugin disable",
    "/plugin remove",
    "/plugin uninstall",
    "/skill",
    "/skill search",
    "/skill install",
    "/skill enable",
    "/skill disable",
    "/skill remove",
    "/undo",
    "/undo list",
    "/memory",
    "/memory get",
    "/memory set",
    "/memory delete",
    "/index",
    "/index rebuild",
    "/mcp",
    "/mcp search",
    "/mcp install",
    "/mcp remove",
    "/lsp",
    "/budget",
    "/exit",
]


class InputHandler:
    """Handles user input with history, auto-suggest, and completion.

    Image paste is detected via two mechanisms:
    - Ctrl+V key binding (prompt_toolkit intercept)
    - Bracketed paste event (terminal-level, covers Cmd+V on macOS)

    Both trigger a clipboard image check automatically.
    """

    def __init__(
        self,
        history_path: Path | str | None = None,
    ) -> None:
        if history_path is not None:
            history: FileHistory | InMemoryHistory = FileHistory(str(history_path))
        else:
            history = InMemoryHistory()

        completer = WordCompleter(SLASH_COMMANDS, pattern=None)

        bindings = KeyBindings()
        self._last_clipboard_image = None
        self._image_pasted = False

        # Ctrl+V: direct key intercept (works on Linux/Windows)
        @bindings.add("c-v")
        def _handle_ctrl_v(event: KeyPressEvent) -> None:
            self._check_and_paste(event)

        # Bracketed paste: intercept terminal paste event (covers Cmd+V on macOS)
        @bindings.add(Keys.BracketedPaste)
        def _handle_bracketed_paste(event: KeyPressEvent) -> None:
            # event.data contains the pasted text
            pasted_text = event.data if hasattr(event, "data") else ""

            # Check clipboard for image
            from llm_code.cli.image import capture_clipboard_image
            img = capture_clipboard_image()
            if img is not None:
                self._last_clipboard_image = img
                self._image_pasted = True
                # Insert marker + any pasted text
                event.app.current_buffer.insert_text("[image pasted] ")
                if pasted_text and pasted_text.strip():
                    event.app.current_buffer.insert_text(pasted_text)
            else:
                # No image — insert pasted text normally
                if pasted_text:
                    event.app.current_buffer.insert_text(pasted_text)

        self._session: PromptSession = PromptSession(
            history=history,
            auto_suggest=AutoSuggestFromHistory(),
            completer=completer,
            key_bindings=bindings,
        )

    def _check_and_paste(self, event: KeyPressEvent) -> None:
        """Check clipboard for image. If found, mark it. Otherwise paste text."""
        from llm_code.cli.image import capture_clipboard_image

        img = capture_clipboard_image()
        if img is not None:
            self._last_clipboard_image = img
            self._image_pasted = True
            event.app.current_buffer.insert_text("[image pasted] ")
        else:
            event.app.current_buffer.paste_clipboard_data(
                event.app.clipboard.get_data()
            )

    def get_pasted_image(self):
        """Return the last pasted image and clear it."""
        img = self._last_clipboard_image
        self._last_clipboard_image = None
        self._image_pasted = False
        return img

    def has_pasted_image(self) -> bool:
        return self._image_pasted

    async def read(self, prompt_text: str = "> ") -> str | None:
        """Read a line of input from the user (async).

        Returns None on Ctrl+C or Ctrl+D (EOFError/KeyboardInterrupt).
        """
        try:
            return await self._session.prompt_async(prompt_text)
        except (EOFError, KeyboardInterrupt):
            return None
