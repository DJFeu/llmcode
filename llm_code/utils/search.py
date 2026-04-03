"""Search utilities for llm-code conversation history."""
from __future__ import annotations

import dataclasses
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_code.api.types import Message


@dataclasses.dataclass(frozen=True)
class SearchResult:
    """A single search match within conversation messages."""

    message_index: int
    line_number: int
    line_text: str
    match_start: int
    match_end: int


def search_messages(
    messages: list[Message],
    query: str,
    case_sensitive: bool = False,
) -> list[SearchResult]:
    """Search through TextBlock content in messages for the given query.

    Only TextBlock.text is searched; ToolUseBlock, ToolResultBlock, and
    ImageBlock content are ignored.

    Args:
        messages: List of Message objects from a session.
        query: The string to search for.
        case_sensitive: When False (default), search is case-insensitive.

    Returns:
        A list of SearchResult instances, one per match, in order of
        appearance (message index, then line number, then match position).
    """
    if not query:
        return []

    from llm_code.api.types import TextBlock

    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = re.compile(re.escape(query), flags)

    results: list[SearchResult] = []

    for msg_idx, message in enumerate(messages):
        for block in message.content:
            if not isinstance(block, TextBlock):
                continue
            text = block.text
            for line_idx, line in enumerate(text.splitlines(), start=1):
                for match in pattern.finditer(line):
                    results.append(
                        SearchResult(
                            message_index=msg_idx,
                            line_number=line_idx,
                            line_text=line,
                            match_start=match.start(),
                            match_end=match.end(),
                        )
                    )

    return results
