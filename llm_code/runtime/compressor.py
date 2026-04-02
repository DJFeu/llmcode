"""ContextCompressor: 4-level progressive context compression."""
from __future__ import annotations

import dataclasses

from llm_code.api.types import Message, TextBlock, ToolResultBlock, ToolUseBlock
from llm_code.runtime.session import Session


class ContextCompressor:
    """Progressively compress a Session context through 4 escalating levels.

    Level 1 — snip_compact: Truncate oversized ToolResultBlock content.
    Level 2 — micro_compact: Remove stale read_file results (keep only latest per path).
    Level 3 — context_collapse: Replace old tool_call+result pairs with one-line summaries.
    Level 4 — auto_compact: Discard all old messages, keep a summary + recent tail.
    """

    def __init__(self, max_result_chars: int = 2000) -> None:
        self._max_result_chars = max_result_chars

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def compress(self, session: Session, max_tokens: int) -> Session:
        """Compress *session* until estimated_tokens() <= max_tokens.

        Applies levels in order, stopping as soon as the budget is met.
        If all 4 levels still cannot reach the budget, the Level-4 result
        is returned (best-effort).
        """
        if session.estimated_tokens() <= max_tokens:
            return session

        session = self._snip_compact(session)
        if session.estimated_tokens() <= max_tokens:
            return session

        session = self._micro_compact(session)
        if session.estimated_tokens() <= max_tokens:
            return session

        session = self._context_collapse(session, keep_recent=6)
        if session.estimated_tokens() <= max_tokens:
            return session

        session = self._auto_compact(session, keep_recent=4)
        return session

    # ------------------------------------------------------------------
    # Level 1
    # ------------------------------------------------------------------

    def _snip_compact(self, session: Session) -> Session:
        """Truncate each ToolResultBlock's content to *max_result_chars*."""
        new_messages: list[Message] = []
        changed = False

        for msg in session.messages:
            new_blocks: list = []
            msg_changed = False
            for block in msg.content:
                if isinstance(block, ToolResultBlock) and len(block.content) > self._max_result_chars:
                    truncated = block.content[: self._max_result_chars]
                    new_blocks.append(dataclasses.replace(block, content=truncated))
                    msg_changed = True
                else:
                    new_blocks.append(block)
            if msg_changed:
                new_messages.append(dataclasses.replace(msg, content=tuple(new_blocks)))
                changed = True
            else:
                new_messages.append(msg)

        if not changed:
            return session
        return dataclasses.replace(session, messages=tuple(new_messages))

    # ------------------------------------------------------------------
    # Level 2
    # ------------------------------------------------------------------

    def _micro_compact(self, session: Session) -> Session:
        """For the same file read multiple times, keep only the latest read_file result.

        Strategy: build a mapping from tool_use_id → file path for all read_file
        ToolUseBlocks.  Then, for each file path, collect the tool_use_ids in order
        and mark all but the last one for removal.  Finally rebuild messages, dropping
        ToolResultBlocks whose tool_use_id is marked.
        """
        # Pass 1: map tool_use_id → path for read_file calls
        id_to_path: dict[str, str] = {}
        for msg in session.messages:
            for block in msg.content:
                if isinstance(block, ToolUseBlock) and block.name == "read_file":
                    path = block.input.get("path", "")
                    if path:
                        id_to_path[block.id] = path

        # For each path, keep only the last tool_use_id
        path_to_ids: dict[str, list[str]] = {}
        for tid, path in id_to_path.items():
            path_to_ids.setdefault(path, []).append(tid)

        stale_ids: set[str] = set()
        for path, ids in path_to_ids.items():
            if len(ids) > 1:
                stale_ids.update(ids[:-1])  # all but the last

        if not stale_ids:
            return session

        # Pass 2: rebuild messages, dropping stale ToolResultBlocks (and their paired ToolUseBlocks)
        new_messages: list[Message] = []
        for msg in session.messages:
            new_blocks = []
            for block in msg.content:
                if isinstance(block, ToolResultBlock) and block.tool_use_id in stale_ids:
                    continue  # drop stale result
                if isinstance(block, ToolUseBlock) and block.id in stale_ids:
                    continue  # drop stale use block too
                new_blocks.append(block)
            if new_blocks:
                new_messages.append(dataclasses.replace(msg, content=tuple(new_blocks)))
            # If a message becomes empty (all blocks dropped), skip it entirely

        return dataclasses.replace(session, messages=tuple(new_messages))

    # ------------------------------------------------------------------
    # Level 3
    # ------------------------------------------------------------------

    def _context_collapse(self, session: Session, keep_recent: int = 6) -> Session:
        """Replace old tool_call+result pairs with one-line summary text.

        Messages in the *keep_recent* tail are kept intact.  Earlier messages
        are converted: ToolUseBlock/ToolResultBlock → summary TextBlock.
        """
        if len(session.messages) <= keep_recent:
            return session

        old_messages = session.messages[:-keep_recent]
        recent_messages = session.messages[-keep_recent:]

        # Collapse old messages into summary lines
        summary_lines: list[str] = []
        for msg in old_messages:
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    args_summary = ", ".join(
                        f"{k}={v!r}" for k, v in list(block.input.items())[:3]
                    )
                    summary_lines.append(f"Used {block.name}({args_summary})")
                elif isinstance(block, ToolResultBlock):
                    # Omit result details — already summarised by the ToolUseBlock line
                    pass
                elif isinstance(block, TextBlock) and block.text.strip():
                    # Keep short summaries of text
                    excerpt = block.text[:80].replace("\n", " ")
                    summary_lines.append(f"[msg] {excerpt}")

        if not summary_lines:
            return dataclasses.replace(session, messages=recent_messages)

        summary_text = "\n".join(summary_lines)
        summary_msg = Message(
            role="user",
            content=(TextBlock(text=f"[Context summary]\n{summary_text}"),),
        )
        return dataclasses.replace(
            session,
            messages=(summary_msg,) + recent_messages,
        )

    # ------------------------------------------------------------------
    # Level 4
    # ------------------------------------------------------------------

    def _auto_compact(self, session: Session, keep_recent: int = 4) -> Session:
        """Replace all old messages with a single summary placeholder + keep tail.

        This mirrors the logic in :func:`llm_code.runtime.compaction.compact_session`.
        """
        if len(session.messages) <= keep_recent:
            return session

        summary_msg = Message(
            role="user",
            content=(TextBlock(text="[Previous conversation summary]\n"),),
        )
        recent = session.messages[-keep_recent:]
        return dataclasses.replace(
            session,
            messages=(summary_msg,) + recent,
        )
