"""Vim operator functions.

Operators take a VimState (and a region) and return a new VimState.
All functions are pure — they never mutate the input state.
"""
from __future__ import annotations

from dataclasses import replace

from llm_code.vim.types import VimMode, VimState, Register


def op_delete(state: VimState, start: int, end: int) -> VimState:
    """Delete text in [start, end) and yank it into register."""
    deleted = state.buffer[start:end]
    new_buf = state.buffer[:start] + state.buffer[end:]
    cursor = min(start, max(0, len(new_buf) - 1))
    return replace(
        state,
        buffer=new_buf,
        cursor=max(0, cursor),
        register=Register(content=deleted),
        undo_stack=state.undo_stack + ((state.buffer, state.cursor),),
    )


def op_change(state: VimState, start: int, end: int) -> VimState:
    """Delete text in [start, end), yank it, and enter INSERT mode."""
    deleted = state.buffer[start:end]
    new_buf = state.buffer[:start] + state.buffer[end:]
    return replace(
        state,
        buffer=new_buf,
        cursor=start,
        mode=VimMode.INSERT,
        register=Register(content=deleted),
        undo_stack=state.undo_stack + ((state.buffer, state.cursor),),
    )


def op_yank(state: VimState, start: int, end: int) -> VimState:
    """Yank text in [start, end) into register without modifying buffer."""
    yanked = state.buffer[start:end]
    return replace(
        state,
        cursor=start,
        register=Register(content=yanked),
    )


def op_delete_line(state: VimState) -> VimState:
    """Delete entire buffer (dd)."""
    return op_delete(state, 0, len(state.buffer))


def op_change_line(state: VimState) -> VimState:
    """Clear entire buffer and enter INSERT (cc)."""
    return op_change(state, 0, len(state.buffer))


def op_yank_line(state: VimState) -> VimState:
    """Yank entire buffer (yy)."""
    return op_yank(state, 0, len(state.buffer))


def op_x(state: VimState) -> VimState:
    """Delete character under cursor."""
    if not state.buffer:
        return state
    end = min(state.cursor + 1, len(state.buffer))
    return op_delete(state, state.cursor, end)


def op_replace(state: VimState, char: str) -> VimState:
    """Replace character under cursor with char."""
    if not state.buffer or state.cursor >= len(state.buffer):
        return state
    new_buf = state.buffer[:state.cursor] + char + state.buffer[state.cursor + 1:]
    return replace(
        state,
        buffer=new_buf,
        undo_stack=state.undo_stack + ((state.buffer, state.cursor),),
    )


def op_tilde(state: VimState) -> VimState:
    """Toggle case of character under cursor and advance."""
    if not state.buffer or state.cursor >= len(state.buffer):
        return state
    ch = state.buffer[state.cursor]
    toggled = ch.lower() if ch.isupper() else ch.upper()
    new_buf = state.buffer[:state.cursor] + toggled + state.buffer[state.cursor + 1:]
    new_cursor = min(state.cursor + 1, max(0, len(new_buf) - 1))
    return replace(
        state,
        buffer=new_buf,
        cursor=new_cursor,
        undo_stack=state.undo_stack + ((state.buffer, state.cursor),),
    )


def op_join(state: VimState) -> VimState:
    """Join lines (J). For single-line input buffer, this is a no-op."""
    return state


def op_put_after(state: VimState) -> VimState:
    """Paste register content after cursor (p)."""
    content = state.register.content
    if not content:
        return state
    insert_pos = state.cursor + 1
    new_buf = state.buffer[:insert_pos] + content + state.buffer[insert_pos:]
    return replace(
        state,
        buffer=new_buf,
        cursor=insert_pos + len(content) - 1,
        undo_stack=state.undo_stack + ((state.buffer, state.cursor),),
    )


def op_put_before(state: VimState) -> VimState:
    """Paste register content before cursor (P)."""
    content = state.register.content
    if not content:
        return state
    insert_pos = state.cursor
    new_buf = state.buffer[:insert_pos] + content + state.buffer[insert_pos:]
    return replace(
        state,
        buffer=new_buf,
        cursor=insert_pos + len(content) - 1,
        undo_stack=state.undo_stack + ((state.buffer, state.cursor),),
    )


def op_open_below(state: VimState) -> VimState:
    """Open line below (o) — append newline and enter INSERT."""
    return replace(
        state,
        cursor=len(state.buffer),
        mode=VimMode.INSERT,
        undo_stack=state.undo_stack + ((state.buffer, state.cursor),),
    )


def op_open_above(state: VimState) -> VimState:
    """Open line above (O) — prepend and enter INSERT."""
    return replace(
        state,
        cursor=0,
        mode=VimMode.INSERT,
        undo_stack=state.undo_stack + ((state.buffer, state.cursor),),
    )


def op_indent_right(state: VimState) -> VimState:
    """Indent right (>>). Add 2 spaces."""
    new_buf = "  " + state.buffer
    return replace(
        state,
        buffer=new_buf,
        cursor=state.cursor + 2,
        undo_stack=state.undo_stack + ((state.buffer, state.cursor),),
    )


def op_indent_left(state: VimState) -> VimState:
    """Indent left (<<). Remove up to 2 leading spaces."""
    if state.buffer.startswith("  "):
        new_buf = state.buffer[2:]
        new_cursor = max(0, state.cursor - 2)
    elif state.buffer.startswith(" "):
        new_buf = state.buffer[1:]
        new_cursor = max(0, state.cursor - 1)
    else:
        return state
    return replace(
        state,
        buffer=new_buf,
        cursor=new_cursor,
        undo_stack=state.undo_stack + ((state.buffer, state.cursor),),
    )
