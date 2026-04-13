"""Clipboard paste handler with text truncation + image support (M15 B4).

Wires Ctrl+V to read the system clipboard and insert either the
full text (short) or a placeholder marker (long text / image) that
registers with a :class:`PastedContentRegistry`.

Optional dependencies
---------------------
- ``pyperclip`` for text clipboard access (falls back to terminal-
  native paste if unavailable).
- ``PIL.ImageGrab`` for image clipboard access (silently skipped
  if PIL is not installed).

Neither dependency is required for a working REPL — the paste
handler degrades gracefully when either is missing.
"""
from __future__ import annotations

import io
from typing import Optional

from prompt_toolkit.buffer import Buffer

from llm_code.view.repl.components.pasted_ref import PastedContentRegistry

__all__ = ["PasteHandler", "handle_paste"]

# Long-text threshold — below this, the full text is inserted
# directly; above, it's replaced with a marker.
_LONG_TEXT_LINES = 8


class PasteHandler:
    """Reads the system clipboard and inserts text/image into a buffer.

    The handler is stateless; pass it into the keybinding Ctrl+V
    handler, which calls :meth:`paste` with the active buffer and
    registry.
    """

    def __init__(self, registry: PastedContentRegistry) -> None:
        self._registry = registry

    def paste(self, buffer: Buffer) -> Optional[str]:
        """Paste the current clipboard contents into ``buffer``.

        Returns a short diagnostic string describing what happened
        (for optional status-line surfacing) or ``None`` on silent
        success / no-op.
        """
        # Try image first — some pastes contain both an image and a
        # text form (e.g. a screenshot with an OCR fallback).
        image_bytes = _read_clipboard_image()
        if image_bytes:
            pc = self._registry.register_image(image_bytes)
            buffer.insert_text(pc.marker)
            return pc.marker

        text = _read_clipboard_text()
        if text is None:
            return None
        if text.count("\n") >= _LONG_TEXT_LINES:
            pc = self._registry.register_text(text)
            buffer.insert_text(pc.marker)
            return pc.marker
        buffer.insert_text(text)
        return None


def _read_clipboard_text() -> Optional[str]:
    try:
        import pyperclip  # type: ignore
    except Exception:
        return None
    try:
        return pyperclip.paste() or None
    except Exception:
        return None


def _read_clipboard_image() -> Optional[bytes]:
    try:
        from PIL import ImageGrab  # type: ignore
    except Exception:
        return None
    try:
        img = ImageGrab.grabclipboard()
    except Exception:
        return None
    if img is None:
        return None
    try:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


def handle_paste(
    buffer: Buffer, registry: PastedContentRegistry
) -> Optional[str]:
    """Convenience entry point for keybinding hooks."""
    return PasteHandler(registry).paste(buffer)
