"""Inline selection list that replaces the input area (M15).

Non-fullscreen PT layouts can't host Float dialogs big enough for
a skill/plugin list. This module provides a ``SelectionListControl``
that renders directly in the main HSplit layout — replacing the
input Window temporarily — so it gets the full terminal height.

Usage flow:
1. Coordinator calls ``start_selection(choices, on_done)``
2. The HSplit swaps the input Window for the selection Window
3. Up/Down navigates, Enter selects, Esc cancels
4. ``on_done(result)`` is called, coordinator restores input Window
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Generic, List, Optional, TypeVar

from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings

T = TypeVar("T")


@dataclass
class SelectionChoice(Generic[T]):
    value: T
    label: str
    hint: str = ""


class InlineSelectState:
    """Mutable state for a running inline selection."""

    def __init__(
        self,
        prompt: str,
        choices: List[SelectionChoice],
        future: asyncio.Future,
    ) -> None:
        self.prompt = prompt
        self.choices = choices
        self.cursor = 0
        self.future = future
        # Visible window of choices (for scrolling in large lists)
        self._scroll_offset = 0
        self._visible_rows = 20  # will be set by coordinator

    def move_cursor(self, delta: int) -> None:
        self.cursor = max(0, min(len(self.choices) - 1, self.cursor + delta))
        # Auto-scroll
        if self.cursor < self._scroll_offset:
            self._scroll_offset = self.cursor
        elif self.cursor >= self._scroll_offset + self._visible_rows:
            self._scroll_offset = self.cursor - self._visible_rows + 1

    def submit(self) -> None:
        if not self.future.done():
            self.future.set_result(self.choices[self.cursor].value)

    def cancel(self) -> None:
        if not self.future.done():
            self.future.set_result(None)

    def render(self) -> FormattedText:
        parts: list[tuple[str, str]] = [
            ("class:dialog.header bold", f" {self.prompt}\n"),
        ]
        end = min(self._scroll_offset + self._visible_rows, len(self.choices))
        if self._scroll_offset > 0:
            parts.append(("class:dialog.hint", f"  ↑ {self._scroll_offset} more above\n"))
        for i in range(self._scroll_offset, end):
            choice = self.choices[i]
            marker = " ▶ " if i == self.cursor else "   "
            style = "reverse" if i == self.cursor else ""
            line = f"{marker}{choice.label}"
            if choice.hint:
                hint_style = "reverse" if i == self.cursor else "fg:#808080"
                parts.append((style, line))
                parts.append((hint_style, f"  {choice.hint}"))
                parts.append(("", "\n"))
            else:
                parts.append((style, line + "\n"))
        remaining = len(self.choices) - end
        if remaining > 0:
            parts.append(("class:dialog.hint", f"  ↓ {remaining} more below\n"))
        parts.append(("fg:#808080", "\n ↑/↓ navigate · Enter select · Esc cancel"))
        return FormattedText(parts)


def build_inline_select_keybindings(
    state_getter: Callable[[], Optional[InlineSelectState]],
    on_done: Callable[[], None],
) -> KeyBindings:
    """Key bindings active while an inline selection is shown."""
    from prompt_toolkit.filters import Condition

    kb = KeyBindings()
    is_active = Condition(lambda: state_getter() is not None)

    @kb.add("up", filter=is_active)
    def _up(event: Any) -> None:
        s = state_getter()
        if s:
            s.move_cursor(-1)

    @kb.add("down", filter=is_active)
    def _down(event: Any) -> None:
        s = state_getter()
        if s:
            s.move_cursor(1)

    @kb.add("enter", filter=is_active)
    def _enter(event: Any) -> None:
        s = state_getter()
        if s:
            s.submit()
            on_done()

    @kb.add("escape", filter=is_active)
    def _esc(event: Any) -> None:
        s = state_getter()
        if s:
            s.cancel()
            on_done()

    return kb
