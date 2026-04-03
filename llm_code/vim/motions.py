"""Vim motion functions.

Each motion takes a VimState and count (or char for f/F/t/T) and returns
the new cursor position (int). Motions are pure functions — they never
mutate state.
"""
from __future__ import annotations

import re
from llm_code.vim.types import VimState


# ── Character motions ─────────────────────────────────────────────

def move_h(state: VimState, count: int) -> int:
    return max(0, state.cursor - count)


def move_l(state: VimState, count: int) -> int:
    max_pos = max(0, len(state.buffer) - 1)
    return min(max_pos, state.cursor + count)


# ── Word motions (small word = split on non-alnum boundary) ───────

_WORD_RE = re.compile(r"[A-Za-z0-9_]+|[^\sA-Za-z0-9_]+")


def move_w(state: VimState, count: int) -> int:
    pos = state.cursor
    for _ in range(count):
        matches = list(_WORD_RE.finditer(state.buffer))
        found = False
        for m in matches:
            if m.start() > pos:
                pos = m.start()
                found = True
                break
        if not found:
            break
    return pos


def move_b(state: VimState, count: int) -> int:
    pos = state.cursor
    for _ in range(count):
        matches = list(_WORD_RE.finditer(state.buffer))
        found = False
        for m in reversed(matches):
            if m.start() < pos:
                pos = m.start()
                found = True
                break
        if not found:
            pos = 0
            break
    return pos


def move_e(state: VimState, count: int) -> int:
    pos = state.cursor
    for _ in range(count):
        matches = list(_WORD_RE.finditer(state.buffer))
        found = False
        for m in matches:
            end = m.end() - 1
            if end > pos:
                pos = end
                found = True
                break
        if not found:
            break
    return pos


# ── WORD motions (big word = split on whitespace only) ────────────

_BIGWORD_RE = re.compile(r"\S+")


def move_W(state: VimState, count: int) -> int:
    pos = state.cursor
    for _ in range(count):
        matches = list(_BIGWORD_RE.finditer(state.buffer))
        found = False
        for m in matches:
            if m.start() > pos:
                pos = m.start()
                found = True
                break
        if not found:
            break
    return pos


def move_B(state: VimState, count: int) -> int:
    pos = state.cursor
    for _ in range(count):
        matches = list(_BIGWORD_RE.finditer(state.buffer))
        found = False
        for m in reversed(matches):
            if m.start() < pos:
                pos = m.start()
                found = True
                break
        if not found:
            pos = 0
            break
    return pos


def move_E(state: VimState, count: int) -> int:
    pos = state.cursor
    for _ in range(count):
        matches = list(_BIGWORD_RE.finditer(state.buffer))
        found = False
        for m in matches:
            end = m.end() - 1
            if end > pos:
                pos = end
                found = True
                break
        if not found:
            break
    return pos


# ── Line motions ──────────────────────────────────────────────────

def move_0(state: VimState) -> int:
    return 0


def move_caret(state: VimState) -> int:
    stripped = state.buffer.lstrip()
    return len(state.buffer) - len(stripped)


def move_dollar(state: VimState) -> int:
    return max(0, len(state.buffer) - 1)


# ── Document motions ─────────────────────────────────────────────

def move_gg(state: VimState) -> int:
    return 0


def move_G(state: VimState) -> int:
    return max(0, len(state.buffer) - 1)


# ── Char search motions ─────────────────────────────────────────

def move_f(state: VimState, char: str) -> int:
    idx = state.buffer.find(char, state.cursor + 1)
    return idx if idx != -1 else state.cursor


def move_F(state: VimState, char: str) -> int:
    idx = state.buffer.rfind(char, 0, state.cursor)
    return idx if idx != -1 else state.cursor


def move_t(state: VimState, char: str) -> int:
    idx = state.buffer.find(char, state.cursor + 1)
    return idx - 1 if idx > 0 else state.cursor


def move_T(state: VimState, char: str) -> int:
    idx = state.buffer.rfind(char, 0, state.cursor)
    return idx + 1 if idx != -1 else state.cursor
