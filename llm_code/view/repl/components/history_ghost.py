"""History ghost text processor for the input buffer (M15 Task B3).

Injects a dim-rendered preview of the most recent history entry
when the buffer is empty. Pressing Tab or Right accepts the ghost
(wired in ``keybindings.py``).
"""
from __future__ import annotations

from typing import Callable, Optional

from prompt_toolkit.document import Document
from prompt_toolkit.layout.processors import (
    Processor,
    Transformation,
    TransformationInput,
)

from llm_code.view.repl import style

__all__ = ["HistoryGhostProcessor"]


class HistoryGhostProcessor(Processor):
    """PT layout processor that appends a dim preview to an empty buffer.

    The processor is a pure function of the buffer's current state +
    the history provider, so it re-evaluates on every redraw — no
    internal mutable state needed.
    """

    def __init__(self, peek: Callable[[], Optional[str]]) -> None:
        self._peek = peek

    def apply_transformation(
        self, transformation_input: TransformationInput
    ) -> Transformation:
        doc: Document = transformation_input.document  # type: ignore[attr-defined]
        fragments = list(transformation_input.fragments)

        # Only show the ghost when the buffer is truly empty AND we're
        # on the first (and only) line.
        if doc.text or transformation_input.lineno != 0:
            return Transformation(fragments)

        preview = self._peek()
        if not preview:
            return Transformation(fragments)

        ghost_style = f"fg:{style.palette.hint_fg} italic"
        # Append the ghost preview as trailing fragments so the cursor
        # stays at position 0.
        ghost_fragments = [(ghost_style, preview)]
        return Transformation(fragments + ghost_fragments)
