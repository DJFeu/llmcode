"""Dialog types shared across all ViewBackend implementations.

Relocated from tui/dialogs/api.py as part of the v2.0.0 view layer
reorganization. The old location remains in place until M11 when
tui/ is deleted; both files contain identical definitions during
the transition, kept in sync by hand.

These types are view-agnostic — the REPL backend, future Telegram/
Discord/Slack/Web backends, and the test scripted backend all
consume and produce Choice / TextValidator instances.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Generic, Optional, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class Choice(Generic[T]):
    """A single selectable option in a ``select`` or ``checklist``.

    ``value`` is what the caller receives when the user picks this
    choice. ``label`` is what the UI displays. ``hint`` is a secondary
    dim line shown beneath the label (optional). ``disabled`` prevents
    the choice from being picked — used for greying out unavailable
    options without hiding them entirely.
    """

    value: T
    label: str
    hint: Optional[str] = None
    disabled: bool = False


# A text validator returns None if the text is valid, or an error message
# string if it isn't. Used by show_text_input() to reject bad input
# with a user-visible error shown inline in the dialog.
TextValidator = Callable[[str], Optional[str]]


class DialogCancelled(Exception):
    """Raised by a backend when a dialog is cancelled (Esc, Ctrl+C,
    window closed, timeout, etc.). Callers should catch this and
    abort the operation that triggered the dialog.
    """


class DialogValidationError(Exception):
    """Raised by ``show_text_input`` when the validator rejects input
    and the backend cannot re-prompt inline. Most backends re-prompt
    rather than raising — this is a fallback for non-interactive
    backends (e.g., scripted test backends with exhausted responses).
    """

    def __init__(self, message: str, attempted_value: str) -> None:
        super().__init__(message)
        self.attempted_value = attempted_value
