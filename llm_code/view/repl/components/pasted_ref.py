"""Pasted-content registry for the input area (M15 Task B4).

Long pasted text and pasted images are stored here by id; the
buffer shows a placeholder like ``[Pasted text #1, 128 lines]``
or ``[Image #2]`` and the dispatcher expands the markers on
submit (or Ctrl+O) to the full content.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Literal, Optional

__all__ = ["PastedContent", "PastedContentRegistry"]


@dataclass
class PastedContent:
    """One pasted blob registered with the input area.

    Fields
    ------
    content_id:
        Monotonic integer. Shown in the marker text.
    kind:
        Either ``"text"`` or ``"image"``.
    lines:
        Line count for ``"text"``; ignored for ``"image"``.
    text:
        Full text body for ``"text"`` kind.
    image_bytes:
        Raw image bytes for ``"image"`` kind.
    marker:
        Cached marker string shown in the buffer.
    """

    content_id: int
    kind: Literal["text", "image"]
    lines: int = 0
    text: str = ""
    image_bytes: bytes = b""
    marker: str = ""


class PastedContentRegistry:
    """In-memory registry with monotonic ids.

    The registry lives on ``AppState`` so the dispatcher can expand
    markers on submit and the Ctrl+O expand path can dump the full
    pasted body into scrollback.
    """

    def __init__(self) -> None:
        self._entries: Dict[int, PastedContent] = {}
        self._next_id = 1

    def register_text(self, body: str) -> PastedContent:
        lines = body.count("\n") + (1 if body else 0)
        pc = PastedContent(
            content_id=self._next_id,
            kind="text",
            lines=lines,
            text=body,
            marker=f"[Pasted text #{self._next_id}, {lines} lines]",
        )
        self._entries[self._next_id] = pc
        self._next_id += 1
        return pc

    def register_image(self, image_bytes: bytes) -> PastedContent:
        pc = PastedContent(
            content_id=self._next_id,
            kind="image",
            image_bytes=image_bytes,
            marker=f"[Image #{self._next_id}]",
        )
        self._entries[self._next_id] = pc
        self._next_id += 1
        return pc

    def get(self, content_id: int) -> Optional[PastedContent]:
        return self._entries.get(content_id)

    def expand(self, buffer_text: str) -> str:
        """Replace all registered markers in ``buffer_text`` with their
        full body (text markers become the raw text; image markers
        stay as-is because images aren't inlineable)."""
        out = buffer_text
        for pc in self._entries.values():
            if pc.kind == "text" and pc.marker in out:
                out = out.replace(pc.marker, pc.text)
        return out

    def count(self) -> int:
        return len(self._entries)
