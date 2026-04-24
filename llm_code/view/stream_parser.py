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

    # v13 Phase C: no-op defaults. The GLM-specific hints that lived
    # here until v2.2.5 now flow through ``65-glm-5.1.toml``
    # ``[parser_hints]`` instead. Callers that need GLM variant-6 /
    # Harmony variant-7 support must pass the relevant kwargs
    # explicitly (or resolve a GLM ``ModelProfile`` and forward its
    # ``custom_close_tags`` / ``call_separator_chars`` tuples).
    _DEFAULT_CUSTOM_CLOSE_TAGS: tuple[str, ...] = ()
    _DEFAULT_CALL_SEPARATOR_CHARS: str = ""
    _DEFAULT_STANDARD_CLOSE_REQUIRED_ON: tuple[str, ...] = ()

    def __init__(
        self,
        *,
        implicit_thinking: bool = False,
        known_tool_names: frozenset[str] | None = None,
        custom_close_tags: tuple[str, ...] | None = None,
        call_separator_chars: str | None = None,
        standard_close_required_on: tuple[str, ...] | None = None,
    ) -> None:
        """Create a new parser.

        When ``implicit_thinking=True``, the parser starts in
        ``_in_think`` state — content at the beginning of the
        stream is classified as THINKING until a ``</think>``
        closes it.

        When ``known_tool_names`` is provided, the parser also
        detects bare ``<tool_name>JSON</...>`` patterns (variant 5)
        and classifies them as TOOL_CALL instead of TEXT.

        v13 profile hints — each kwarg is an override; pass ``None``
        (the default) to opt into the v2.3.0 no-op defaults (only
        ``</tool_call>`` counts as a close tag). Callers that need
        GLM variant 6 / Harmony variant 7 support must supply the
        values themselves, typically by forwarding the resolved
        ``ModelProfile``'s ``custom_close_tags``,
        ``call_separator_chars`` and the variant-registry-derived
        ``standard_close_required_on`` tuple.

        - ``custom_close_tags`` — fallback close tags when
          ``</tool_call>`` is not yet visible. Empty tuple (the
          v2.3.0 default) means only ``</tool_call>`` terminates
          ``<tool_call>`` blocks.
        - ``call_separator_chars`` — chars ``.lstrip``ed after a
          custom close tag before the next ``<tool_call>`` search.
          Empty string (the default) means no separator stripping.
        - ``standard_close_required_on`` — if any substring here
          appears in the ``<tool_call>`` buffer, wait for the real
          ``</tool_call>`` and ignore custom close tags. Empty
          tuple (the default) disables this guard.

        Passing ``None`` (or omitting the kwarg entirely) is
        equivalent to passing the class-level default — both paths
        produce the v2.3.0 no-op behaviour.
        """
        self._buffer: str = ""
        self._in_think: bool = implicit_thinking
        self._in_tool_call: bool = False
        self._in_bare_tool: bool = False  # inside a <known_tool_name> block
        self._bare_tool_tag: str = ""     # the opening tag name
        self._known_tool_names = known_tool_names or frozenset()
        # Profile hints — ``None`` and omitting the kwarg both fall
        # back to the v2.3.0 no-op defaults. Callers pass explicit
        # tuples / strings when they need non-default behaviour.
        self._custom_close_tags: tuple[str, ...] = (
            custom_close_tags
            if custom_close_tags is not None
            else self._DEFAULT_CUSTOM_CLOSE_TAGS
        )
        self._call_separator_chars: str = (
            call_separator_chars
            if call_separator_chars is not None
            else self._DEFAULT_CALL_SEPARATOR_CHARS
        )
        self._standard_close_required_on: tuple[str, ...] = (
            standard_close_required_on
            if standard_close_required_on is not None
            else self._DEFAULT_STANDARD_CLOSE_REQUIRED_ON
        )

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
        if self._in_bare_tool:
            # Unterminated bare tool — try to parse what we have
            block_text = f"<{self._bare_tool_tag}>{self._buffer}</{self._bare_tool_tag}>"
            parsed = parse_tool_calls(block_text, None, known_tool_names=self._known_tool_names)
            if parsed:
                for p in parsed:
                    events.append(StreamEvent(kind=StreamEventKind.TOOL_CALL, tool_call=p))
            elif self._buffer:
                events.append(StreamEvent(kind=StreamEventKind.TEXT, text=self._buffer))
            self._buffer = ""
            self._in_bare_tool = False
            return events
        if self._in_tool_call:
            # GLM-5.1 emits ``<tool_call>NAME}{JSON}</arg_value>`` +
            # (``→``+``<tool_call>``…) chained tool calls without a
            # trailing ``</tool_call>`` — the stream ends inside a
            # still-open block from the standard parser's point of
            # view. Try the full-buffer parse first so those calls
            # reach the runtime instead of silently downgrading to
            # TEXT. The buffer still carries the opening ``<tool_call>``
            # marker (``_step`` keeps it so this fallback sees the
            # full block).
            parsed = parse_tool_calls(
                self._buffer, None, known_tool_names=self._known_tool_names
            )
            if parsed:
                for p in parsed:
                    events.append(
                        StreamEvent(kind=StreamEventKind.TOOL_CALL, tool_call=p)
                    )
                _log.info(
                    "StreamParser.flush: recovered %d tool call(s) from "
                    "unterminated <tool_call> block (%d chars, likely "
                    "GLM variant 6)",
                    len(parsed),
                    len(self._buffer),
                )
                self._buffer = ""
                self._in_tool_call = False
                return events

            # Nothing parsed — salvage the body as TEXT so the user
            # at least sees what the model was generating. Strip the
            # leading ``<tool_call>`` marker for display.
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
            # Close-tag selection — profile-driven (v13 Phase A).
            #
            # 1. Standard ``</tool_call>`` ALWAYS wins when visible —
            #    this is a universal rule, independent of variant.
            # 2. Else, if any substring in
            #    ``standard_close_required_on`` is present in the
            #    buffer, wait for the real ``</tool_call>`` (avoids
            #    eating interior tags that belong to a variant body,
            #    e.g. Harmony variant 7's ``</arg_value>``).
            # 3. Else, scan for the earliest ``custom_close_tags``
            #    occurrence and use that as the close.
            # 4. If none found, wait for more data.
            end_std = buf.find("</tool_call>")
            if end_std != -1:
                end, close_tag = end_std, "</tool_call>"
            else:
                if any(
                    marker in buf
                    for marker in self._standard_close_required_on
                ):
                    return False
                # Scan each custom close tag; use the earliest.
                earliest: tuple[int, str] | None = None
                for tag in self._custom_close_tags:
                    pos = buf.find(tag)
                    if pos == -1:
                        continue
                    if earliest is None or pos < earliest[0]:
                        earliest = (pos, tag)
                if earliest is None:
                    return False
                end, close_tag = earliest
            close_len = len(close_tag)
            block = buf[: end + close_len]
            rest = buf[end + close_len :]
            if close_tag != "</tool_call>" and self._call_separator_chars:
                # Consume any configured separator characters so the
                # NEXT iteration starts cleanly at the following
                # ``<tool_call>`` (if any). For GLM-5.1 variant 6 the
                # separator is U+2192 ``→`` + whitespace.
                rest = rest.lstrip(self._call_separator_chars)
            self._buffer = rest
            self._in_tool_call = False
            parsed = parse_tool_calls(block, None)
            if parsed:
                for p in parsed:
                    events.append(
                        StreamEvent(kind=StreamEventKind.TOOL_CALL, tool_call=p)
                    )
            else:
                events.append(
                    StreamEvent(kind=StreamEventKind.TOOL_CALL, tool_call=None)
                )
            return True

        if self._in_bare_tool:
            # Inside a <known_tool_name> block — scan for any </tag>
            import re as _re
            close_match = _re.search(r"</[a-zA-Z_][a-zA-Z0-9_]*>", buf)
            if close_match is None:
                return False  # wait for more data
            # Extract the full block and parse it
            block_text = f"<{self._bare_tool_tag}>{buf[:close_match.start()]}</{self._bare_tool_tag}>"
            self._buffer = buf[close_match.end():]
            self._in_bare_tool = False
            parsed = parse_tool_calls(block_text, None, known_tool_names=self._known_tool_names)
            if parsed:
                for p in parsed:
                    events.append(StreamEvent(kind=StreamEventKind.TOOL_CALL, tool_call=p))
            else:
                events.append(StreamEvent(kind=StreamEventKind.TOOL_CALL, tool_call=None))
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

        # Detect bare <tool_name> tags (variant 5) for known tools
        if self._known_tool_names:
            import re as _re
            for m in _re.finditer(r"<([a-zA-Z_][a-zA-Z0-9_]*)>", buf):
                tag_name = m.group(1)
                if tag_name in self._known_tool_names:
                    candidates.append((m.start(), m.group(0), f"bare_tool:{tag_name}"))
                    break  # only need the first match

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
        elif kind.startswith("bare_tool:"):
            tool_name = kind.split(":", 1)[1]
            self._buffer = buf[len(tag) :]
            self._in_bare_tool = True
            self._bare_tool_tag = tool_name
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
