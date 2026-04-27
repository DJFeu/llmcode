"""Server-Sent Events (SSE) parser for streaming LLM responses."""
from __future__ import annotations

import json
import re
from typing import AsyncIterator, Iterator

# Split on blank lines — handles both \n\n and \r\n\r\n
_BLOCK_SEPARATOR = re.compile(r'\r?\n\r?\n')


def parse_sse_events(raw: str) -> Iterator[dict]:
    """Parse a raw SSE string and yield each event as a parsed dict.

    Rules:
    - Split on blank-line boundaries (\\n\\n or \\r\\n\\r\\n).
    - Lines starting with ':' are comments — skipped.
    - Lines starting with 'data: ' contribute to the event data.
    - Multiple data lines within one block are joined with '\\n'.
    - 'data: [DONE]' stops iteration.
    - Non-data fields (event:, id:, retry:) are silently ignored.
    - JSON is parsed and yielded as a dict.
    """
    for block in _BLOCK_SEPARATOR.split(raw):
        block = block.strip()
        if not block:
            continue

        data_parts: list[str] = []
        for line in re.split(r'\r?\n', block):
            if line.startswith(':'):
                # Comment line — skip
                continue
            if line.startswith('data:'):
                # Strip the field name and a single optional space
                value = line[5:]
                if value.startswith(' '):
                    value = value[1:]
                if value == '[DONE]':
                    return
                data_parts.append(value)
            # event:, id:, retry: — ignore

        if not data_parts:
            continue

        joined = '\n'.join(data_parts)
        try:
            yield json.loads(joined)
        except json.JSONDecodeError:
            # Malformed JSON — skip silently (could log in production)
            continue


async def aparse_sse_events_from_lines(
    line_iter: AsyncIterator[str],
) -> AsyncIterator[dict]:
    """Async incremental SSE parser (v2.6.1 M3).

    Consumes an async iterator of *lines* (no trailing newlines, the
    shape ``httpx.Response.aiter_lines`` produces) and yields one
    parsed event dict per complete SSE block. Each yield happens as
    soon as the block boundary is observed — no whole-response
    buffering.

    Rules match :func:`parse_sse_events`:
    - Blank line ends an event block.
    - ``:`` lines are comments.
    - ``data: <value>`` lines accumulate into the event payload.
    - ``data: [DONE]`` ends the stream.
    - Multi-line ``data:`` payloads join with ``\\n``.
    - Non-data fields (``event:``, ``id:``, ``retry:``) are ignored.
    - Malformed JSON is silently skipped.

    The terminating ``[DONE]`` marker stops iteration via ``return``,
    which translates to ``StopAsyncIteration`` in an async context —
    callers get a clean end-of-stream.
    """
    data_parts: list[str] = []

    def _emit() -> dict | None:
        if not data_parts:
            return None
        joined = "\n".join(data_parts)
        try:
            return json.loads(joined)
        except json.JSONDecodeError:
            return None

    async for line in line_iter:
        # ``aiter_lines`` strips trailing CR/LF but may yield "" for the
        # blank line that ends an SSE block.
        if line == "":
            event = _emit()
            data_parts = []
            if event is not None:
                yield event
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            value = line[5:]
            if value.startswith(" "):
                value = value[1:]
            if value == "[DONE]":
                return
            data_parts.append(value)
        # event:, id:, retry: ignored

    # End of stream — flush any pending block (server didn't send a
    # trailing blank line). httpx aiter_lines normally guarantees one
    # but defensive flush keeps malformed streams parseable.
    event = _emit()
    if event is not None:
        yield event
