"""Ctrl+O expand/collapse registry for long content blocks (M15 Task C6).

Every long-content renderer (tool output, thinking, pasted text,
long assistant messages, structured diffs) registers itself here
with a preview. Pressing Ctrl+O appends the full body below an
``── expanded: <kind> #id ──`` divider; a second press appends a
re-collapse divider + preview. This trades in-place redraw for
an append-only audit trail — the pragmatic compromise for a
non-fullscreen prompt_toolkit layout.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Literal, Optional

from rich.console import Console
from rich.text import Text

from llm_code.view.repl import style

__all__ = ["TruncatedBlock", "TruncationRegistry"]


BlockKind = Literal[
    "tool_output",
    "thinking",
    "pasted_text",
    "assistant_message",
    "diff",
]


@dataclass
class TruncatedBlock:
    block_id: int
    kind: BlockKind
    preview_lines: int
    full_body: str
    current_state: Literal["collapsed", "expanded"] = "collapsed"
    marker_text: str = ""


class TruncationRegistry:
    """Append-only registry of long content blocks with Ctrl+O toggle."""

    def __init__(self) -> None:
        self._blocks: List[TruncatedBlock] = []
        self._next_id = 1

    def register(
        self,
        kind: BlockKind,
        full_body: str,
        *,
        preview_lines: int = 10,
    ) -> TruncatedBlock:
        line_count = full_body.count("\n") + (1 if full_body else 0)
        remaining = max(0, line_count - preview_lines)
        marker = (
            f"[… {remaining} more lines · Ctrl+O to expand]"
            if remaining > 0
            else ""
        )
        block = TruncatedBlock(
            block_id=self._next_id,
            kind=kind,
            preview_lines=preview_lines,
            full_body=full_body,
            marker_text=marker,
        )
        self._blocks.append(block)
        self._next_id += 1
        return block

    def count_truncated(self) -> int:
        """Return the number of blocks that still have hidden content.

        Only blocks whose preview clipped content count (blocks that
        are already ``"expanded"`` or whose full body was shorter
        than the preview threshold are excluded).
        """
        return sum(
            1
            for b in self._blocks
            if b.current_state == "collapsed" and b.marker_text
        )

    def toggle_latest(self, console: Console) -> Optional[TruncatedBlock]:
        """Toggle the most recent not-already-toggled block."""
        target = self._latest_toggleable()
        if target is None:
            return None
        self._toggle(target, console)
        return target

    def toggle(
        self, block_id: int, console: Console
    ) -> Optional[TruncatedBlock]:
        for b in self._blocks:
            if b.block_id == block_id:
                self._toggle(b, console)
                return b
        return None

    def clear(self) -> None:
        self._blocks.clear()
        self._next_id = 1

    # --- internal helpers ---

    def _latest_toggleable(self) -> Optional[TruncatedBlock]:
        for b in reversed(self._blocks):
            if b.marker_text:  # has hidden content
                return b
        return None

    def _toggle(self, block: TruncatedBlock, console: Console) -> None:
        if block.current_state == "collapsed":
            divider = Text(
                f"── expanded: {block.kind} #{block.block_id} ──",
                style=style.palette.hint_fg,
            )
            body = Text(block.full_body, style=style.palette.system_fg)
            console.print(divider)
            console.print(body)
            block.current_state = "expanded"
        else:
            divider = Text(
                f"── re-collapsed: {block.kind} #{block.block_id} ──",
                style=style.palette.hint_fg,
            )
            preview_lines = block.full_body.splitlines()[: block.preview_lines]
            preview = Text(
                "\n".join(preview_lines), style=style.palette.system_fg
            )
            console.print(divider)
            console.print(preview)
            block.current_state = "collapsed"
