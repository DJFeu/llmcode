"""InputArea — multi-line input with slash popover, history, vim mode.

Owns a prompt_toolkit Buffer configured with:
- Multi-line editing (auto-expanding height up to 12 rows)
- SlashCompleter completer for /command popover
- History wiring via Ctrl+Up/Down (bindings in keybindings.py)
- Optional vim mode (toggled by dispatcher via set_vim_mode())

The coordinator embeds this into its Layout. In M3 the coordinator had
a raw Buffer + placeholder Window; M4 swaps those for an InputArea
instance + its managed Window.
"""
from __future__ import annotations

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout.containers import (
    ConditionalContainer,
    Float,
    Window,
)
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.layout.menus import CompletionsMenu

from llm_code.view.repl.components.slash_popover import SlashCompleter


MIN_ROWS = 1
MAX_ROWS = 12


class InputArea:
    """Self-contained multi-line input component."""

    def __init__(self) -> None:
        self._completer = SlashCompleter()
        self.buffer = Buffer(
            multiline=True,
            completer=self._completer,
            complete_while_typing=True,
        )
        self._vim_mode = False

    @property
    def completer(self) -> SlashCompleter:
        return self._completer

    def refresh_completions(self) -> None:
        """Re-scan the slash command registry. Call after plugin load."""
        self._completer.refresh()

    def set_vim_mode(self, enabled: bool) -> None:
        """Toggle vim mode on the underlying buffer.

        prompt_toolkit implements vim mode at the Application level,
        not the Buffer level, so the coordinator is responsible for
        actually flipping Application.editing_mode. This method just
        tracks the desired state for the coordinator to query.
        """
        self._vim_mode = enabled

    @property
    def vim_mode(self) -> bool:
        return self._vim_mode

    def build_window(self) -> Window:
        """Construct the main input Window.

        Height is dynamic: min 1 row, max 12 rows, sized to the buffer's
        current content + 1 (for the trailing prompt cursor).
        """
        def _height() -> int:
            line_count = self.buffer.text.count("\n") + 1
            return max(MIN_ROWS, min(line_count, MAX_ROWS))

        return Window(
            content=BufferControl(
                buffer=self.buffer,
                focus_on_click=True,
            ),
            height=_height,
            wrap_lines=True,
            style="class:input",
        )

    def build_popover_float(self) -> Float:
        """Construct the Float that hosts the slash-completion popover.

        The popover only shows when the completer has matches AND the
        input starts with '/'. Otherwise it's hidden (no dropdown
        appears during regular typing).
        """
        has_slash = Condition(lambda: self.buffer.text.startswith("/"))

        return Float(
            xcursor=True,
            ycursor=True,
            content=ConditionalContainer(
                content=CompletionsMenu(max_height=8, scroll_offset=1),
                filter=has_slash,
            ),
        )
