"""Strip ANSI escape sequences from text."""
from __future__ import annotations

import re

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
_ANSI_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences (SGR, cursor, OSC) from text.

    Defensive: malformed sequences are left as-is rather than crashing.
    """
    if not text or "\x1b" not in text:
        return text
    try:
        text = _ANSI_OSC_RE.sub("", text)
        text = _ANSI_RE.sub("", text)
    except Exception:
        return text
    return text
