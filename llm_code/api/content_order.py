"""Wave2-1a P1: ordering invariant for assistant Message content.

Within a single ``Message(role="assistant")``, all ``ThinkingBlock``s
must precede the first non-thinking block. Multiple thinking blocks may
appear consecutively (a provider is free to split a long reasoning
trace into chunks). This module ships a pure validator that enforces
the invariant without depending on runtime state — it is called from
the message-assembly path in P3 and from the DB rehydration path in P5,
so a corrupted row cannot silently poison a live session.

The validator is intentionally permissive about one thing: a message
with zero thinking blocks passes trivially, so the whole existing
codebase (which never constructs thinking blocks yet) stays valid.
"""
from __future__ import annotations

from llm_code.api.types import ContentBlock, ThinkingBlock


class ThinkingOrderError(ValueError):
    """Raised when a ThinkingBlock appears after a non-thinking block.

    The exception carries the offending index and the block types at
    and adjacent to the violation so a future assembly-layer bug can
    be debugged from the traceback alone without re-running.
    """

    def __init__(
        self,
        *,
        index: int,
        offending_type: str,
        preceding_type: str,
    ) -> None:
        self.index = index
        self.offending_type = offending_type
        self.preceding_type = preceding_type
        super().__init__(
            f"ThinkingBlock at index {index} appears after a "
            f"{preceding_type}; all thinking blocks must precede the "
            f"first non-thinking block (found {offending_type} after "
            f"{preceding_type})."
        )


def validate_assistant_content_order(
    blocks: tuple[ContentBlock, ...],
) -> None:
    """Raise :class:`ThinkingOrderError` if the ordering invariant is violated.

    The invariant: all ``ThinkingBlock`` instances in ``blocks`` must
    appear before the first non-``ThinkingBlock``. Empty tuples and
    tuples without any thinking blocks always pass.

    This is a pure function: no I/O, no state, no side effects.
    """
    seen_non_thinking = False
    first_non_thinking_type = ""
    for idx, block in enumerate(blocks):
        if isinstance(block, ThinkingBlock):
            if seen_non_thinking:
                raise ThinkingOrderError(
                    index=idx,
                    offending_type=type(block).__name__,
                    preceding_type=first_non_thinking_type,
                )
        else:
            if not seen_non_thinking:
                seen_non_thinking = True
                first_non_thinking_type = type(block).__name__
