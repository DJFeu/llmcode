"""Server-Sent Events (SSE) parser for streaming LLM responses."""
from __future__ import annotations

import json
import re
from typing import Iterator

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
