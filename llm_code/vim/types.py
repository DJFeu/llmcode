"""Core vim types — mode, state, register, parsed command."""
from __future__ import annotations

import enum
from dataclasses import dataclass, replace


class VimMode(enum.Enum):
    NORMAL = "normal"
    INSERT = "insert"


@dataclass(frozen=True)
class Register:
    content: str = ""


@dataclass(frozen=True)
class ParsedCommand:
    count: int = 1
    operator: str | None = None
    motion: str | None = None
    text_object: str | None = None


@dataclass(frozen=True)
class VimState:
    buffer: str
    cursor: int
    mode: VimMode
    register: Register
    undo_stack: tuple[tuple[str, int], ...] = ()
    last_command: ParsedCommand | None = None
    pending_keys: str = ""

    def with_cursor(self, pos: int) -> VimState:
        clamped = max(0, min(pos, len(self.buffer)))
        return replace(self, cursor=clamped)

    def with_buffer(self, buf: str, cursor: int | None = None) -> VimState:
        c = cursor if cursor is not None else self.cursor
        clamped = max(0, min(c, len(buf)))
        return replace(self, buffer=buf, cursor=clamped)

    def with_mode(self, mode: VimMode) -> VimState:
        return replace(self, mode=mode)

    def with_register(self, content: str) -> VimState:
        return replace(self, register=Register(content=content))

    def push_undo(self) -> VimState:
        entry = (self.buffer, self.cursor)
        return replace(self, undo_stack=self.undo_stack + (entry,))

    def pop_undo(self) -> VimState:
        if not self.undo_stack:
            return self
        *rest, (buf, cur) = self.undo_stack
        return replace(self, buffer=buf, cursor=cur, undo_stack=tuple(rest))


def initial_state(buffer: str) -> VimState:
    return VimState(
        buffer=buffer,
        cursor=len(buffer),
        mode=VimMode.INSERT,
        register=Register(),
    )
