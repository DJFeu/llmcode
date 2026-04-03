"""Vim state machine — key handling, mode transitions, repeat, undo.

The central function is handle_key(state, key) -> VimState.
It dispatches based on current mode and accumulated pending_keys.
"""
from __future__ import annotations

from dataclasses import replace

from llm_code.vim.types import VimMode, VimState, ParsedCommand
from llm_code.vim.motions import (
    move_h, move_l, move_w, move_b, move_e,
    move_W, move_B, move_E,
    move_0, move_caret, move_dollar,
    move_gg, move_G,
    move_f, move_F, move_t, move_T,
)
from llm_code.vim.operators import (
    op_delete, op_change, op_yank,
    op_delete_line, op_change_line, op_yank_line,
    op_x, op_replace, op_tilde, op_join,
    op_put_after, op_put_before,
    op_open_below, op_open_above,
    op_indent_right, op_indent_left,
)
from llm_code.vim.text_objects import select_text_object


_MOTION_KEYS = frozenset("hlwbeWBE0^$")
_OPERATOR_KEYS = frozenset("dcy")
_SINGLE_CHAR_OPS = frozenset("xrR~JpPoO")


def handle_key(state: VimState, key: str) -> VimState:
    """Process a single key press and return the new VimState."""
    if state.mode == VimMode.INSERT:
        return _handle_insert(state, key)
    return _handle_normal(state, key)


def _handle_insert(state: VimState, key: str) -> VimState:
    """INSERT mode: type characters, Esc to exit."""
    if key == "\x1b":  # Escape
        cursor = max(0, state.cursor - 1)
        return replace(state, mode=VimMode.NORMAL, cursor=cursor)
    if key in ("\x7f", "\x08"):  # Backspace
        if state.cursor == 0:
            return state
        new_buf = state.buffer[:state.cursor - 1] + state.buffer[state.cursor:]
        return replace(state, buffer=new_buf, cursor=state.cursor - 1)
    # Regular character insertion
    new_buf = state.buffer[:state.cursor] + key + state.buffer[state.cursor:]
    return replace(state, buffer=new_buf, cursor=state.cursor + 1)


def _handle_normal(state: VimState, key: str) -> VimState:
    """NORMAL mode: motions, operators, mode switches."""
    pending = state.pending_keys + key

    # ── Count prefix ──────────────────────────────────────────────
    if pending.isdigit() and pending != "0":
        return replace(state, pending_keys=pending)

    # ── Parse accumulated pending keys ────────────────────────────
    count = 1
    rest = pending
    # Extract leading digits as count
    i = 0
    while i < len(rest) and rest[i].isdigit() and (i > 0 or rest[i] != "0"):
        i += 1
    if i > 0:
        count = int(rest[:i])
        rest = rest[i:]

    if not rest:
        return replace(state, pending_keys=pending)

    # ── Dot repeat ────────────────────────────────────────────────
    if rest == ".":
        if state.last_command is not None:
            return _replay_command(replace(state, pending_keys=""), state.last_command)
        return replace(state, pending_keys="")

    # ── Undo ──────────────────────────────────────────────────────
    if rest == "u":
        return replace(state.pop_undo(), pending_keys="")

    # ── Mode switches ─────────────────────────────────────────────
    if rest == "i":
        return replace(state, mode=VimMode.INSERT, pending_keys="")
    if rest == "a":
        cursor = min(state.cursor + 1, len(state.buffer))
        return replace(state, mode=VimMode.INSERT, cursor=cursor, pending_keys="")
    if rest == "A":
        return replace(state, mode=VimMode.INSERT, cursor=len(state.buffer), pending_keys="")
    if rest == "I":
        pos = move_caret(state)
        return replace(state, mode=VimMode.INSERT, cursor=pos, pending_keys="")

    # ── Single-key operators ──────────────────────────────────────
    if rest == "x":
        result = state
        for _ in range(count):
            result = op_x(result)
        cmd = ParsedCommand(count=count, operator="x")
        return replace(result, pending_keys="", last_command=cmd)

    if rest == "~":
        result = state
        for _ in range(count):
            result = op_tilde(result)
        return replace(result, pending_keys="")

    if rest == "J":
        return replace(op_join(state), pending_keys="")

    if rest == "p":
        return replace(op_put_after(state), pending_keys="")

    if rest == "P":
        return replace(op_put_before(state), pending_keys="")

    if rest == "o":
        return replace(op_open_below(state), pending_keys="")

    if rest == "O":
        return replace(op_open_above(state), pending_keys="")

    # ── Operator waiting for motion/text-object ───────────────────
    if len(rest) == 1 and rest in _OPERATOR_KEYS:
        return replace(state, pending_keys=pending)

    # ── Line-wise doubled operators: dd, cc, yy ──────────────────
    if rest == "dd":
        result = op_delete_line(state)
        cmd = ParsedCommand(count=count, operator="dd")
        return replace(result, pending_keys="", last_command=cmd)
    if rest == "cc":
        result = op_change_line(state)
        return replace(result, pending_keys="")
    if rest == "yy":
        result = op_yank_line(state)
        return replace(result, pending_keys="")

    # ── Indent ────────────────────────────────────────────────────
    if rest == ">>":
        result = state
        for _ in range(count):
            result = op_indent_right(result)
        return replace(result, pending_keys="")
    if rest == "<<":
        result = state
        for _ in range(count):
            result = op_indent_left(result)
        return replace(result, pending_keys="")
    if rest in (">", "<"):
        return replace(state, pending_keys=pending)

    # ── Replace: r{char} ─────────────────────────────────────────
    if len(rest) == 1 and rest == "r":
        return replace(state, pending_keys=pending)
    if len(rest) == 2 and rest[0] == "r":
        result = op_replace(state, rest[1])
        return replace(result, pending_keys="")

    # ── Char-search: f/F/t/T{char} ──────────────────────────────
    if len(rest) == 1 and rest in "fFtT":
        return replace(state, pending_keys=pending)
    if len(rest) == 2 and rest[0] in "fFtT":
        motion_fn = {"f": move_f, "F": move_F, "t": move_t, "T": move_T}[rest[0]]
        new_cursor = motion_fn(state, rest[1])
        return replace(state, cursor=new_cursor, pending_keys="")

    # ── g-prefixed: gg, gj, gk ──────────────────────────────────
    if rest == "g":
        return replace(state, pending_keys=pending)
    if rest == "gg":
        return replace(state, cursor=move_gg(state), pending_keys="")
    if rest == "G":
        return replace(state, cursor=move_G(state), pending_keys="")

    # ── Operator + text object (e.g., "diw", "ca(") ─────────────
    if len(rest) >= 3 and rest[0] in _OPERATOR_KEYS and rest[1] in "ia":
        text_obj = rest[1:]
        region = select_text_object(state, text_obj)
        if region is None:
            return replace(state, pending_keys="")
        start, end = region
        op_fn = {"d": op_delete, "c": op_change, "y": op_yank}[rest[0]]
        result = op_fn(state, start, end)
        cmd = ParsedCommand(count=count, operator=rest[0], text_object=text_obj)
        return replace(result, pending_keys="", last_command=cmd)

    # ── Operator + motion (e.g., "dw", "cw", "yw") ──────────────
    if len(rest) >= 2 and rest[0] in _OPERATOR_KEYS:
        motion_key = rest[1:]
        # cw/cW acts like ce/cE in vim (stops before trailing whitespace)
        effective_motion = motion_key
        if rest[0] == "c" and motion_key == "w":
            effective_motion = "e"
        elif rest[0] == "c" and motion_key == "W":
            effective_motion = "E"
        new_cursor = _resolve_motion(state, effective_motion, count)
        if new_cursor is not None:
            start = min(state.cursor, new_cursor)
            # For e/E motions, end is inclusive so add 1
            end = max(state.cursor, new_cursor)
            if effective_motion in ("e", "E"):
                end = end + 1
            op_fn = {"d": op_delete, "c": op_change, "y": op_yank}[rest[0]]
            result = op_fn(state, start, end)
            cmd = ParsedCommand(count=count, operator=rest[0], motion=motion_key)
            return replace(result, pending_keys="", last_command=cmd)
        # Unknown motion after operator — wait for more input or reset
        if len(rest) < 4:
            return replace(state, pending_keys=pending)
        return replace(state, pending_keys="")

    # ── Simple motions ────────────────────────────────────────────
    new_cursor = _resolve_motion(state, rest, count)
    if new_cursor is not None:
        return replace(state, cursor=new_cursor, pending_keys="")

    # ── Unknown key sequence — reset pending ──────────────────────
    return replace(state, pending_keys="")


def _resolve_motion(state: VimState, key: str, count: int) -> int | None:
    """Resolve a motion key to a cursor position, or None if not a motion."""
    if key == "h":
        return move_h(state, count)
    if key == "l":
        return move_l(state, count)
    if key == "w":
        return move_w(state, count)
    if key == "b":
        return move_b(state, count)
    if key == "e":
        return move_e(state, count)
    if key == "W":
        return move_W(state, count)
    if key == "B":
        return move_B(state, count)
    if key == "E":
        return move_E(state, count)
    if key == "0":
        return move_0(state)
    if key == "^":
        return move_caret(state)
    if key == "$":
        return move_dollar(state)
    return None


def _replay_command(state: VimState, cmd: ParsedCommand) -> VimState:
    """Replay a recorded command (dot repeat)."""
    if cmd.operator == "x":
        result = state
        for _ in range(cmd.count):
            result = op_x(result)
        return replace(result, last_command=cmd)
    if cmd.operator == "dd":
        return replace(op_delete_line(state), last_command=cmd)
    if cmd.operator and cmd.text_object:
        region = select_text_object(state, cmd.text_object)
        if region is None:
            return state
        start, end = region
        op_fn = {"d": op_delete, "c": op_change, "y": op_yank}[cmd.operator]
        return replace(op_fn(state, start, end), last_command=cmd)
    if cmd.operator and cmd.motion:
        new_cursor = _resolve_motion(state, cmd.motion, cmd.count)
        if new_cursor is None:
            return state
        start = min(state.cursor, new_cursor)
        end = max(state.cursor, new_cursor)
        op_fn = {"d": op_delete, "c": op_change, "y": op_yank}[cmd.operator]
        return replace(op_fn(state, start, end), last_command=cmd)
    return state
