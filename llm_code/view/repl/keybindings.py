"""Factory for the REPL's prompt_toolkit KeyBindings.

Single place where every key -> action mapping lives. The coordinator
calls build_keybindings() during Application construction; components
can extend the returned KeyBindings instance via merge_key_bindings()
for their local bindings (slash popover navigation, vim mode toggle,
voice hotkey in M9, etc.).
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.key_binding import KeyBindings

from llm_code.view.repl.history import PromptHistory


def build_keybindings(
    *,
    input_buffer: Buffer,
    history: PromptHistory,
    on_submit: Callable[[str], None],
    on_exit: Callable[[], None],
    on_voice_toggle: Optional[Callable[[], None]] = None,
) -> KeyBindings:
    """Construct the full KeyBindings set for the REPL.

    Args:
        input_buffer: the PT Buffer the bindings operate on
        history: PromptHistory for Ctrl+Up/Down recall
        on_submit: callback fired when Enter is pressed on non-empty text
        on_exit: callback fired on Ctrl+D (empty) / second Ctrl+C (empty)
        on_voice_toggle: optional; if set, Ctrl+G and Ctrl+Space fire it

    Note on Esc handling:
        We deliberately do NOT bind plain Esc here. prompt_toolkit treats
        Esc as the prefix byte for Alt+<key> sequences, so a bare Esc
        binding produces a ~0.5s timeout on every Alt+X press. Components
        that need Esc (popover dismiss, dialog cancel) register their
        own narrow bindings with a Condition filter instead.
    """
    kb = KeyBindings()

    # === Submit / newline ===

    @kb.add("enter")
    def _submit(event: Any) -> None:
        text = input_buffer.text.strip()
        if not text:
            return
        input_buffer.reset()
        on_submit(text)
        event.app.invalidate()

    # Shift+Enter has no portable representation — most terminals don't
    # distinguish it from Enter. We expose Ctrl+J (Linux convention) and
    # Alt+Enter (macOS convention) for explicit newline insertion.
    @kb.add("c-j")                 # Linux convention for newline
    @kb.add("escape", "enter")     # Alt+Enter (macOS convention)
    def _newline(event: Any) -> None:
        input_buffer.insert_text("\n")

    # === Exit ===

    @kb.add("c-d")
    def _ctrl_d(event: Any) -> None:
        if not input_buffer.text:
            on_exit()
            event.app.exit()

    @kb.add("c-c")
    def _ctrl_c(event: Any) -> None:
        if input_buffer.text:
            input_buffer.reset()
        else:
            on_exit()
            event.app.exit()

    # === Clear line ===

    @kb.add("c-u")
    def _clear_line(event: Any) -> None:
        input_buffer.reset()

    # === History recall (Ctrl+Up/Down) ===

    @kb.add("c-up")
    def _history_prev(event: Any) -> None:
        current = input_buffer.text
        recalled = history.prev(current=current)
        if recalled is not None:
            input_buffer.text = recalled
            input_buffer.cursor_position = len(recalled)

    @kb.add("c-down")
    def _history_next(event: Any) -> None:
        recalled = history.next()
        if recalled is not None:
            input_buffer.text = recalled
            input_buffer.cursor_position = len(recalled)

    # === Voice hotkey (Ctrl+G / Ctrl+Space) ===

    if on_voice_toggle is not None:
        @kb.add("c-g")
        @kb.add("c-@")  # prompt_toolkit encodes Ctrl+Space as Ctrl+@
        def _voice(event: Any) -> None:
            on_voice_toggle()

    return kb
