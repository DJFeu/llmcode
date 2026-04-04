"""Vim text object selectors.

Each function returns (start, end) as an exclusive range [start, end),
or None if the text object cannot be found at the cursor position.
"""
from __future__ import annotations

import re
from llm_code.vim.types import VimState

_WORD_RE = re.compile(r"[A-Za-z0-9_]+|[^\sA-Za-z0-9_]+")
_BIGWORD_RE = re.compile(r"\S+")

_BRACKET_PAIRS = {
    "(": ("(", ")"),
    ")": ("(", ")"),
    "[": ("[", "]"),
    "]": ("[", "]"),
    "{": ("{", "}"),
    "}": ("{", "}"),
    "<": ("<", ">"),
    ">": ("<", ">"),
}


def select_text_object(
    state: VimState, obj: str
) -> tuple[int, int] | None:
    """Select a text object region.

    Args:
        state: Current vim state.
        obj: Text object string, e.g. "iw", "a(", "i\"".

    Returns:
        (start, end) exclusive range, or None if not found.
    """
    if len(obj) < 2:
        return None

    kind = obj[0]  # 'i' (inner) or 'a' (around)
    target = obj[1]

    if target in ("w",):
        return _select_word(state, kind, small=True)
    if target in ("W",):
        return _select_word(state, kind, small=False)
    if target in ('"', "'"):
        return _select_quoted(state, kind, target)
    if target in _BRACKET_PAIRS:
        return _select_bracket(state, kind, target)

    return None


def _select_word(
    state: VimState, kind: str, *, small: bool
) -> tuple[int, int] | None:
    pattern = _WORD_RE if small else _BIGWORD_RE
    for m in pattern.finditer(state.buffer):
        if m.start() <= state.cursor < m.end():
            start, end = m.start(), m.end()
            if kind == "a":
                # Include trailing whitespace
                while end < len(state.buffer) and state.buffer[end] == " ":
                    end += 1
            return (start, end)
    return None


def _select_quoted(
    state: VimState, kind: str, quote: str
) -> tuple[int, int] | None:
    buf = state.buffer
    # Find opening quote before or at cursor
    open_idx = buf.rfind(quote, 0, state.cursor + 1)
    if open_idx == -1:
        return None
    # Find closing quote after opening
    close_idx = buf.find(quote, open_idx + 1)
    if close_idx == -1 or close_idx < state.cursor:
        return None
    if kind == "i":
        return (open_idx + 1, close_idx)
    return (open_idx, close_idx + 1)


def _select_bracket(
    state: VimState, kind: str, target: str
) -> tuple[int, int] | None:
    open_ch, close_ch = _BRACKET_PAIRS[target]
    buf = state.buffer

    # For angle brackets, use simple nearest-neighbor search
    # (find nearest > to the left and < to the right of cursor)
    if open_ch == "<":
        open_idx = buf.rfind(open_ch, 0, state.cursor + 1)
        if open_idx == -1:
            return None
        close_idx = buf.find(close_ch, state.cursor)
        if close_idx == -1:
            return None
        if kind == "i":
            return (open_idx + 1, close_idx)
        return (open_idx, close_idx + 1)

    # Search backward for opening bracket (with nesting support)
    depth = 0
    open_idx = -1
    for i in range(state.cursor, -1, -1):
        if buf[i] == close_ch:
            depth += 1
        elif buf[i] == open_ch:
            if depth == 0:
                open_idx = i
                break
            depth -= 1

    if open_idx == -1:
        return None

    # Search forward for matching closing bracket
    depth = 0
    close_idx = -1
    for i in range(open_idx, len(buf)):
        if buf[i] == open_ch:
            depth += 1
        elif buf[i] == close_ch:
            depth -= 1
            if depth == 0:
                close_idx = i
                break

    if close_idx == -1:
        return None

    if kind == "i":
        return (open_idx + 1, close_idx)
    return (open_idx, close_idx + 1)
