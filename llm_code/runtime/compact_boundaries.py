"""Compact-boundary message helpers (M2).

Claude Code attaches ``cache_control: ephemeral`` to the system
message that Anthropic's API treats as a cache breakpoint. After
compaction the first post-compact message is ideal for the same
treatment — the summary content is stable across turns, so letting
the API cache it saves 80%+ of prompt tokens on follow-up turns.
"""
from __future__ import annotations


def build_boundary_message(
    *,
    summary: str,
    previous_msg_count: int,
    cache_control: bool = True,
) -> dict:
    """Build the post-compact marker user message.

    Returned as a plain dict so both Anthropic- and OpenAI-compat
    request builders can pass it through; the second layer reshapes
    ``content`` blocks for their respective wire formats.
    """
    text = (
        f"[Previous conversation summary from {previous_msg_count} messages]\n"
        f"{summary}"
    )
    block: dict = {"type": "text", "text": text}
    if cache_control:
        block["cache_control"] = {"type": "ephemeral"}
    return {"role": "user", "content": [block]}
