"""Wave2-6: Dialog Protocol + common types.

Pure types, no I/O. Every concrete backend (scripted, headless,
textual) satisfies this Protocol. Callers depend on the Protocol
only so swapping backends at runtime is a one-line change.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Generic, Protocol, Sequence, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class Choice(Generic[T]):
    """A single selectable option in a ``select`` or ``checklist``.

    ``value`` is what the caller gets back when the user picks this
    choice. ``label`` is what the UI shows. ``hint`` is a secondary
    dim line shown under the label (optional). ``disabled`` prevents
    the choice from being picked — useful for greying out unavailable
    options without hiding them.
    """

    value: T
    label: str
    hint: str | None = None
    disabled: bool = False


class DialogCancelled(Exception):
    """Raised by a backend when the user cancels a prompt.

    This is the "dialog returned nothing" signal for backends that
    have no sensible default (e.g. text input with no default value,
    or a select with every option disabled). Callers should catch
    it explicitly rather than relying on None.
    """


class DialogValidationError(ValueError):
    """Raised by a backend's ``text`` validator when the user input
    is rejected. Backends that support retry-on-invalid should catch
    this internally and reprompt; backends that do not (scripted,
    headless pipe mode) propagate it to the caller."""


# A validator returns None for "OK" or a string describing the error.
TextValidator = Callable[[str], str | None]


class Dialogs(Protocol):
    """The single interface every call site should depend on.

    All four methods are async so a future textual backend can
    ``await`` on screen push/pop without blocking the event loop.
    Scripted and headless backends are also async for uniformity —
    their implementations just return immediately.

    Method contract summary:

    * ``confirm`` — binary yes/no with optional ``danger=True`` for
      visually-emphasized destructive operations. ``default`` is
      returned when the user just presses Enter / in non-interactive
      headless mode with no TTY.
    * ``select`` — pick one value from ``choices``. ``default`` must
      match one of the choice values if provided.
    * ``text`` — free-form string input with optional ``multiline``
      and an optional ``validator``.
    * ``checklist`` — pick zero or more values from ``items`` with
      optional ``min_select`` / ``max_select`` bounds.

    Backends MAY raise :class:`DialogCancelled` from any method when
    the user aborts. Callers should wrap the call in try/except if
    cancellation is a valid outcome.
    """

    async def confirm(
        self,
        prompt: str,
        *,
        default: bool = False,
        danger: bool = False,
    ) -> bool:
        ...

    async def select(
        self,
        prompt: str,
        choices: Sequence[Choice[T]],
        *,
        default: T | None = None,
    ) -> T:
        ...

    async def text(
        self,
        prompt: str,
        *,
        default: str = "",
        multiline: bool = False,
        validator: TextValidator | None = None,
    ) -> str:
        ...

    async def checklist(
        self,
        prompt: str,
        items: Sequence[Choice[T]],
        *,
        min_select: int = 0,
        max_select: int | None = None,
    ) -> list[T]:
        ...
