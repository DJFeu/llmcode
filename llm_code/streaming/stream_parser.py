"""Canonical streaming parser for model output.

Consumed by both the TUI rendering pipeline (for display) and the
runtime tool-call dispatcher (for execution), so both paths see the
exact same interpretation of the stream.

Handles:
- ``<think>...</think>`` blocks (and the vLLM template-injected variant
  where the opening ``<think>\\n`` is in the prompt prefix and only the
  closing tag appears in the stream)
- ``<tool_call>...</tool_call>`` blocks in all three Hermes sub-formats
  (full form, template-truncated with ``<parameter=>``, template-truncated
  with JSON args)
- Tag boundaries that straddle chunk boundaries (e.g. ``<thi`` arrives
  in one chunk and ``nk>`` in the next)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from llm_code.tools.parsing import ParsedToolCall, parse_tool_calls

_log = logging.getLogger(__name__)

# Longest tag string we need to reserve at a chunk tail so we don't
# accidentally emit the start of a tag as plain text.
_TAG_RESERVE = len("</tool_call>")


class StreamEventKind(Enum):
    TEXT = "text"
    THINKING = "thinking"
    TOOL_CALL = "tool_call"


@dataclass(frozen=True)
class StreamEvent:
    kind: StreamEventKind
    text: str = ""
    tool_call: ParsedToolCall | None = None


class StreamParser:
    """Chunked-input parser that emits ``StreamEvent``s.

    Call ``feed(chunk)`` for each delta from the provider stream; it
    returns the events produced by that chunk. Incomplete tags at chunk
    boundaries stay in the buffer until the next ``feed()`` or
    ``flush()``.
    """

    def __init__(self) -> None:
        self._buffer: str = ""
        self._in_think: bool = False
        self._in_tool_call: bool = False

    def feed(self, chunk: str) -> list[StreamEvent]:
        self._buffer += chunk
        events: list[StreamEvent] = []
        # Loop until no more progress can be made on the buffer.
        while self._step(events):
            pass
        return events

    def flush(self) -> list[StreamEvent]:
        """Emit any residual buffered content. Call at end-of-stream so
        trailing text that doesn't include a tag still reaches the caller.

        Bug fix — unterminated tool_call salvage: previously an
        unterminated ``<tool_call>...`` block at end-of-stream was
        **silently dropped**, which caused the TUI to lose any
        content the model had generated inside that tag. This is
        observable when a provider truncates mid-tag (for example,
        ``max_tokens`` hit inside a partial ``<tool_call>{...``) or
        when a model emits a spurious opening tag that it then
        fails to close. The symptom seen in the field: user asks
        for "今日新聞三則", model generates the intro line, opens a
        stray ``<tool_call>`` (or has an existing one that never
        closes), then either truncates or continues with the news
        items inside the never-closed tag. Everything inside was
        thrown away by ``flush()`` with zero diagnostic.

        Now we salvage the buffered content as a TEXT event so the
        user at least sees what the model was actually generating.
        The leading ``<tool_call>`` marker is stripped so the text
        reads naturally. A warning is logged at the same time so
        a ``-v`` run captures the event.
        """
        events: list[StreamEvent] = []
        if not self._buffer:
            return events
        if self._in_think:
            # Unterminated thinking at end-of-stream — emit what we have.
            events.append(
                StreamEvent(kind=StreamEventKind.THINKING, text=self._buffer)
            )
            _log.warning(
                "StreamParser.flush: unterminated <think> block, "
                "emitting %d chars as THINKING",
                len(self._buffer),
            )
            self._buffer = ""
            self._in_think = False
            return events
        if self._in_tool_call:
            # Unterminated tool_call — salvage the body as TEXT so
            # the user at least sees what the model was generating.
            # Strip the opening ``<tool_call>`` marker if it's still
            # at the head of the buffer (the buffer keeps it there
            # so ``parse_tool_calls`` can see the full block when
            # the closer arrives; here we strip it for display).
            salvaged = self._buffer
            if salvaged.startswith("<tool_call>"):
                salvaged = salvaged[len("<tool_call>") :]
            if salvaged:
                events.append(
                    StreamEvent(kind=StreamEventKind.TEXT, text=salvaged)
                )
            _log.warning(
                "StreamParser.flush: unterminated <tool_call> block, "
                "salvaging %d chars as TEXT (was: silently dropped)",
                len(salvaged),
            )
            self._buffer = ""
            self._in_tool_call = False
            return events
        events.append(StreamEvent(kind=StreamEventKind.TEXT, text=self._buffer))
        self._buffer = ""
        return events

    def _step(self, events: list[StreamEvent]) -> bool:
        """Try to emit one event from the buffer. Return True if progress
        was made (events appended or buffer advanced)."""
        buf = self._buffer
        if not buf:
            return False

        if self._in_tool_call:
            end = buf.find("</tool_call>")
            if end == -1:
                return False
            block = buf[: end + len("</tool_call>")]
            self._buffer = buf[end + len("</tool_call>") :]
            self._in_tool_call = False
            parsed = parse_tool_calls(block, None)
            if parsed:
                for p in parsed:
                    events.append(
                        StreamEvent(kind=StreamEventKind.TOOL_CALL, tool_call=p)
                    )
            else:
                # We consumed a <tool_call>...</tool_call> block but the
                # parser returned zero calls (unknown format variant).
                # Emit a sentinel TOOL_CALL event with tool_call=None so
                # the TUI can still set its saw_tool_call diagnostic flag
                # and show the "model tried to call a tool" message
                # instead of the misleading "thinking ate output" one.
                events.append(
                    StreamEvent(kind=StreamEventKind.TOOL_CALL, tool_call=None)
                )
            return True

        if self._in_think:
            end = buf.find("</think>")
            if end == -1:
                return False
            thinking_text = buf[:end]
            self._buffer = buf[end + len("</think>") :]
            self._in_think = False
            if thinking_text:
                events.append(
                    StreamEvent(kind=StreamEventKind.THINKING, text=thinking_text)
                )
            return True

        # Not in any tag — look for the next tag opener OR a lone
        # ``</think>`` (which indicates the vLLM chat template injected
        # an implicit opening tag as part of the assistant prompt prefix).
        think_open = buf.find("<think>")
        think_close = buf.find("</think>")
        tc_open = buf.find("<tool_call>")
        candidates: list[tuple[int, str, str]] = []
        if think_open != -1:
            candidates.append((think_open, "<think>", "think"))
        if think_close != -1 and (think_open == -1 or think_close < think_open):
            # Bare </think> (implicit open) — everything before it is
            # treated as thinking content.
            candidates.append((think_close, "</think>", "implicit_think_end"))
        if tc_open != -1:
            candidates.append((tc_open, "<tool_call>", "tool_call"))

        if not candidates:
            # No tag visible yet. Emit as much text as we can, holding
            # back a trailing window in case a tag is split at the end.
            safe_emit = self._safe_text_cut(buf)
            if safe_emit == 0:
                return False
            events.append(StreamEvent(kind=StreamEventKind.TEXT, text=buf[:safe_emit]))
            self._buffer = buf[safe_emit:]
            return True

        candidates.sort(key=lambda c: c[0])
        pos, tag, kind = candidates[0]

        if kind == "implicit_think_end":
            # Content before the bare </think> is implicit thinking.
            thinking_text = buf[:pos]
            self._buffer = buf[pos + len("</think>") :]
            if thinking_text:
                events.append(
                    StreamEvent(kind=StreamEventKind.THINKING, text=thinking_text)
                )
            return True

        # Emit any text before the opening tag as TEXT.
        if pos > 0:
            events.append(StreamEvent(kind=StreamEventKind.TEXT, text=buf[:pos]))
            self._buffer = buf[pos:]
            return True

        # Tag is at position 0 — advance into the corresponding state.
        if kind == "think":
            self._buffer = buf[len("<think>") :]
            self._in_think = True
        elif kind == "tool_call":
            # Leave the opening ``<tool_call>`` in the buffer so
            # parse_tool_calls sees the full block when we find the close.
            self._in_tool_call = True
        return True

    @staticmethod
    def _safe_text_cut(buf: str) -> int:
        """Return the highest index such that ``buf[:index]`` contains no
        partial tag prefix. Used when we want to emit buffered text but
        must hold back the tail in case it's the start of a tag split
        across chunks."""
        if len(buf) <= _TAG_RESERVE:
            # Entirely within the reserve window — check if any ``<``
            # is present at all; if not, the whole buffer is safe.
            return 0 if "<" in buf else len(buf)
        last_lt = buf.rfind("<", len(buf) - _TAG_RESERVE)
        if last_lt == -1:
            return len(buf)
        return last_lt
