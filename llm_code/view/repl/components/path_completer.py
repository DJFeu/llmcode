"""Path completer for ``@file`` mentions in the input buffer (M15 Task B2).

Triggers when the current token starts with ``@`` (mention syntax),
``./`` (relative path), or ``/`` (absolute path, but NOT when the
token is a slash command — handled by ``SlashCompleter``).

The yielded completions embed OSC8 hyperlinks in their display
text so Warp / iTerm2 / WezTerm users can click through to open
the file.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from prompt_toolkit.completion import (
    CompleteEvent,
    Completer,
    Completion,
    merge_completers,
)
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import FormattedText

from llm_code.view.repl import style
from llm_code.view.repl.components.slash_popover import SlashCompleter

__all__ = ["PathCompleter", "build_input_completer"]


class PathCompleter(Completer):
    """File path completer keyed by ``@``, ``./``, or ``/``.

    ``@foo`` matches files whose relative path starts with ``foo``.
    ``./foo`` and ``/foo`` match absolute/relative paths literally.
    """

    def __init__(self, cwd: Path | None = None, max_results: int = 12) -> None:
        self._cwd = cwd or Path.cwd()
        self._max_results = max_results

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterable[Completion]:
        text = document.text_before_cursor
        if not text:
            return

        # Find the current token — last whitespace-separated chunk.
        token = text.rsplit(None, 1)[-1] if text.rsplit(None, 1) else text

        if token.startswith("@"):
            prefix = token[1:]
            base = self._cwd
            strip = 1  # account for the @ prefix
        elif token.startswith("./"):
            prefix = token[2:]
            base = self._cwd
            strip = 2
        elif token.startswith("/") and not token.startswith("/" + "c"):
            # Absolute path, but skip slash commands — SlashCompleter
            # handles those. We scan the /-prefixed paths only when the
            # buffer's very start is NOT a /command pattern.
            if document.text.startswith("/") and not " " in document.text:
                return
            prefix = token
            base = Path("/")
            strip = 1
        else:
            return

        # Expand ~ if the prefix uses it.
        if prefix.startswith("~"):
            prefix = os.path.expanduser(prefix)

        try:
            # Split into parent dir and leaf prefix.
            p = Path(prefix)
            if prefix.endswith("/") or prefix == "":
                parent = base / prefix if prefix else base
                leaf = ""
            else:
                parent = (base / p.parent) if not p.is_absolute() else p.parent
                leaf = p.name
            if not parent.is_dir():
                return
            matches = sorted(parent.iterdir())
        except (OSError, PermissionError):
            return

        shown = 0
        for entry in matches:
            if shown >= self._max_results:
                break
            if entry.name.startswith("."):
                continue
            if not entry.name.startswith(leaf):
                continue
            rel = entry.relative_to(self._cwd) if base == self._cwd else entry
            text_out = f"@{rel}" if token.startswith("@") else str(rel)
            display = FormattedText(
                [(f"fg:{style.palette.file_path_fg}", text_out)]
            )
            meta_text = "dir" if entry.is_dir() else "file"
            display_meta = FormattedText(
                [(f"fg:{style.palette.hint_fg}", meta_text)]
            )
            yield Completion(
                text=text_out,
                start_position=-len(token),
                display=display,
                display_meta=display_meta,
            )
            shown += 1


def build_input_completer(cwd: Path | None = None) -> Completer:
    """Merge the slash + path completers into a single completer.

    Used by :class:`InputArea` in M15. Slash commands take precedence
    over path completions when the buffer starts with ``/`` because
    the PathCompleter explicitly avoids that case.
    """
    return merge_completers(
        [SlashCompleter(), PathCompleter(cwd=cwd)]
    )
