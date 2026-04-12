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

from llm_code.view.repl.components.history_ghost import HistoryGhostProcessor
from llm_code.view.repl.components.path_completer import (
    build_input_completer,
)
from llm_code.view.repl.components.slash_popover import SlashCompleter
from llm_code.view.repl.history import PromptHistory


MIN_ROWS = 1
MAX_ROWS = 12


class InputArea:
    """Self-contained multi-line input component."""

    def __init__(self, history: PromptHistory | None = None) -> None:
        self._slash_completer = SlashCompleter()
        # Merged completer (slash + @file path). The separate
        # SlashCompleter reference is kept for ``refresh_completions``
        # so plugin reloads can re-scan the command registry without
        # rebuilding the merged completer.
        self._completer = build_input_completer()
        self.buffer = Buffer(
            multiline=True,
            completer=self._completer,
            complete_while_typing=True,
        )
        self._history = history
        self._vim_mode = False

    @property
    def completer(self):
        return self._completer

    def refresh_completions(self) -> None:
        """Re-scan the slash command registry. Call after plugin load."""
        self._slash_completer.refresh()
        # Rebuild the merged completer so the PathCompleter picks up
        # any cwd changes on refresh.
        self._completer = build_input_completer()
        self.buffer.completer = self._completer

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

        processors = []
        if self._history is not None:
            processors.append(HistoryGhostProcessor(peek=self._history.peek_latest))
        return Window(
            content=BufferControl(
                buffer=self.buffer,
                focus_on_click=True,
                input_processors=processors,
            ),
            height=_height,
            wrap_lines=True,
            style="class:input",
        )

    def build_popover_float(self) -> Float:
        """Construct the Float that hosts the slash-completion popover.

        Uses a single-column vertical :class:`CompletionsMenu` with
        ``display_meta`` showing command descriptions — matching
        Claude Code's vertical list style. ``max_height=16`` so
        the user sees enough commands to navigate comfortably with
        ↑/↓/Tab/Right.
        """
        has_slash = Condition(lambda: self.buffer.text.startswith("/"))

        return Float(
            xcursor=True,
            ycursor=True,
            content=ConditionalContainer(
                content=CompletionsMenu(max_height=16, scroll_offset=2),
                filter=has_slash,
            ),
        )
