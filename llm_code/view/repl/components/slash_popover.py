"""Slash-command autocomplete completer for the REPL input.

Triggers when the input buffer starts with `/`. Produces completions
from llm_code.cli.commands.COMMAND_REGISTRY + one-line descriptions.
Respects spec section 6.7:

- Max 8 visible rows (4 in short terminals)
- Overflow shows "v N more"
- Tab accepts selected completion (does not submit)
- Esc dismisses popover + preserves typed text
- Bare Up/Down within popover navigates (default PT Completion behavior)
- Ctrl+Up/Down fall through to history recall (handled in keybindings.py)
"""
from __future__ import annotations

from typing import Iterable

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document

from llm_code.cli.commands import COMMAND_REGISTRY


def _build_slash_entries() -> list[tuple[str, str]]:
    """Return sorted (name, description) pairs for every registered command."""
    return sorted(
        (f"/{cmd.name}", cmd.description or "")
        for cmd in COMMAND_REGISTRY
    )


class SlashCompleter(Completer):
    """Completer that yields slash-command completions when the input
    starts with '/'.

    Not active for any other prefix — regular typing (no '/' prefix)
    produces no completions and the popover stays hidden.
    """

    def __init__(self) -> None:
        self._entries = _build_slash_entries()

    def refresh(self) -> None:
        """Re-scan COMMAND_REGISTRY (called after plugin load/unload)."""
        self._entries = _build_slash_entries()

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterable[Completion]:
        text = document.text_before_cursor
        if not text.startswith("/"):
            return

        # Match prefix against the command name portion only (no args)
        command_prefix = text.split()[0] if text.split() else text
        for name, description in self._entries:
            if name.startswith(command_prefix):
                yield Completion(
                    text=name,
                    start_position=-len(command_prefix),
                    display=name,
                    display_meta=description,
                )
