"""Tool output buffer with backpressure (M1).

Tools that stream lots of output (bash on a build, grep across a
massive repo) can easily hand the runtime more text than the model's
context can absorb. :class:`ToolResultBuffer` caps the accumulated
text by character count and line count, appending a clear marker
when truncation kicks in so the model knows something was cut.
"""
from __future__ import annotations


class ToolResultBuffer:
    TRUNCATION_MARKER = "\n... [output truncated] ..."

    def __init__(
        self,
        *,
        max_chars: int = 100_000,
        max_lines: int = 5_000,
    ) -> None:
        self._max_chars = max_chars
        self._max_lines = max_lines
        self._chunks: list[str] = []
        self._size = 0
        self._lines = 0
        self._truncated = False

    # ── Status ─────────────────────────────────────────────────────

    @property
    def truncated(self) -> bool:
        return self._truncated

    @property
    def size(self) -> int:
        return self._size

    @property
    def line_count(self) -> int:
        return self._lines

    # ── Mutation ──────────────────────────────────────────────────

    def append(self, text: str) -> None:
        if self._truncated:
            return
        newlines = text.count("\n")
        # Line cap first: if this text would push past the allowed
        # line count, drop it entirely and emit only the marker.
        if self._lines + newlines > self._max_lines:
            self._chunks.append(self.TRUNCATION_MARKER)
            self._truncated = True
            return
        # Char cap second: partial append up to remaining room + marker.
        if self._size + len(text) > self._max_chars:
            room = max(0, self._max_chars - self._size)
            if room > 0:
                self._chunks.append(text[:room])
                self._size += room
            self._chunks.append(self.TRUNCATION_MARKER)
            self._truncated = True
            return
        self._chunks.append(text)
        self._size += len(text)
        self._lines += newlines

    def text(self) -> str:
        return "".join(self._chunks)

    def flush(self) -> str:
        out = self.text()
        self._chunks.clear()
        self._size = 0
        self._lines = 0
        self._truncated = False
        return out
