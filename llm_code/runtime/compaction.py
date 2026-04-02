"""Context compaction: trim old session messages when the context grows too large."""
from __future__ import annotations

import dataclasses

from llm_code.api.types import Message, TextBlock
from llm_code.runtime.session import Session


def needs_compaction(session: Session, threshold: int = 80000) -> bool:
    """Return True when the session's estimated token count exceeds *threshold*."""
    return session.estimated_tokens() > threshold


def compact_session(
    session: Session,
    keep_recent: int = 4,
    summary: str = "",
) -> Session:
    """Return a compacted session keeping only the most recent *keep_recent* messages.

    If the session has <= keep_recent messages, the original session is returned
    unchanged.  Otherwise a single summary message is prepended to the last
    *keep_recent* messages.
    """
    if len(session.messages) <= keep_recent:
        return session

    summary_msg = Message(
        role="user",
        content=(TextBlock(text=f"[Previous conversation summary]\n{summary}"),),
    )
    recent = session.messages[-keep_recent:]
    new_messages = (summary_msg,) + recent
    return dataclasses.replace(session, messages=new_messages)
