"""Incremental Markdown renderer for streaming LLM output."""
from __future__ import annotations

import re

from rich.console import Console
from rich.markdown import Markdown
from rich.syntax import Syntax

_CODE_BLOCK_RE = re.compile(r"^```(\w*)\n(.*?)```\s*$", re.DOTALL)


class IncrementalMarkdownRenderer:
    """Renders streaming token output incrementally using Rich.

    Strategy:
    - Accumulate tokens in a buffer.
    - After each feed, attempt to flush completed blocks:
        * Code block: text between opening ``` and closing ```.
        * Paragraph / heading / list: text terminated by \\n\\n.
    - finish() flushes whatever remains.
    """

    def __init__(self, console: Console) -> None:
        self._console = console
        self._buffer = ""
        self._in_code_block = False

    def feed(self, token: str) -> None:
        """Accumulate a token and try to flush completed blocks."""
        self._buffer += token
        self._try_flush()

    def finish(self) -> None:
        """Flush all remaining buffered content."""
        if self._buffer.strip():
            self._render_block(self._buffer)
        self._buffer = ""
        self._in_code_block = False

    def _try_flush(self) -> None:
        """Detect and render complete blocks from the buffer."""
        while True:
            if self._in_code_block:
                # Look for closing ```
                close_idx = self._buffer.find("```", 3)  # skip opening ```
                if close_idx == -1:
                    break  # code block not yet closed
                # Include everything up to and including the closing ```
                end = close_idx + 3
                # Consume optional trailing newlines
                while end < len(self._buffer) and self._buffer[end] in ("\n",):
                    end += 1
                block = self._buffer[:end]
                self._buffer = self._buffer[end:]
                self._in_code_block = False
                self._render_block(block)
            else:
                # Check if we're entering a code block
                if self._buffer.startswith("```"):
                    self._in_code_block = True
                    continue  # re-check with in_code_block=True

                # Look for paragraph boundary (\n\n)
                para_idx = self._buffer.find("\n\n")
                if para_idx == -1:
                    break  # no complete paragraph yet

                block = self._buffer[: para_idx + 2]
                self._buffer = self._buffer[para_idx + 2 :]

                # After consuming a paragraph, the remainder might start a code block
                if self._buffer.startswith("```"):
                    self._in_code_block = True

                stripped = block.strip()
                if stripped:
                    self._render_block(stripped)

    def _render_block(self, block: str) -> None:
        """Render a single block — code block as Syntax, else as Markdown."""
        stripped = block.strip()
        if not stripped:
            return

        m = _CODE_BLOCK_RE.match(stripped)
        if m:
            lang = m.group(1) or "text"
            code = m.group(2)
            self._console.print(Syntax(code, lang, theme="monokai"))
        else:
            self._console.print(Markdown(stripped))
