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

    Cache-aware: tracks which message indices have been sent to the API (cached).
    Compression levels prefer removing non-cached messages first to preserve
    API-side prompt cache hits.
    """

    def __init__(self, max_result_chars: int = 2000) -> None:
        self._max_result_chars = max_result_chars
        self._cached_indices: set[int] = set()

    # ------------------------------------------------------------------
    # Cache tracking
    # ------------------------------------------------------------------

    def mark_as_cached(self, message_indices: set[int]) -> None:
        """Mark which message indices have been sent to the API (cache hits)."""
        self._cached_indices.update(message_indices)

    def _is_cached(self, index: int) -> bool:
        """Return True if the message at *index* has been sent to the API."""
        return index in self._cached_indices

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
        """Truncate each ToolResultBlock's content to *max_result_chars*.

        Cache-aware: truncate non-cached messages first.  If no non-cached
        messages are over-budget, fall through to truncating cached ones too.
        """
        new_messages: list[Message] = []
        changed = False

        # First pass: truncate only non-cached oversized results
        for idx, msg in enumerate(session.messages):
            new_blocks: list = []
            msg_changed = False
            for block in msg.content:
                if (
                    isinstance(block, ToolResultBlock)
                    and len(block.content) > self._max_result_chars
                    and not self._is_cached(idx)
                ):
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

        interim = dataclasses.replace(session, messages=tuple(new_messages)) if changed else session

        # Second pass: also truncate cached oversized results (fallback)
        final_messages: list[Message] = []
        second_changed = False
        for idx, msg in enumerate(interim.messages):
            new_blocks = []
            msg_changed = False
            for block in msg.content:
                if isinstance(block, ToolResultBlock) and len(block.content) > self._max_result_chars:
                    truncated = block.content[: self._max_result_chars]
                    new_blocks.append(dataclasses.replace(block, content=truncated))
                    msg_changed = True
                else:
                    new_blocks.append(block)
            if msg_changed:
                final_messages.append(dataclasses.replace(msg, content=tuple(new_blocks)))
                second_changed = True
            else:
                final_messages.append(msg)

        if not changed and not second_changed:
            return session
        if second_changed:
            return dataclasses.replace(session, messages=tuple(final_messages))
        return interim

    # ------------------------------------------------------------------
    # Level 2
    # ------------------------------------------------------------------

    def _micro_compact(self, session: Session) -> Session:
        """For the same file read multiple times, keep only the latest read_file result.

        Strategy: build a mapping from tool_use_id → file path for all read_file
        ToolUseBlocks.  Then, for each file path, collect the tool_use_ids in order
        and mark all but the last one for removal.  Finally rebuild messages, dropping
        ToolResultBlocks whose tool_use_id is marked.

        Cache-aware: prefer removing non-cached stale reads first.  If no
        non-cached duplicates exist, fall back to removing cached ones.
        """
        # Pass 1: map tool_use_id → (path, message_index) for read_file calls
        id_to_path: dict[str, str] = {}
        id_to_msg_index: dict[str, int] = {}
        for msg_idx, msg in enumerate(session.messages):
            for block in msg.content:
                if isinstance(block, ToolUseBlock) and block.name == "read_file":
                    path = block.input.get("path", "")
                    if path:
                        id_to_path[block.id] = path
                        id_to_msg_index[block.id] = msg_idx

        # For each path, keep only the last tool_use_id
        path_to_ids: dict[str, list[str]] = {}
        for tid, path in id_to_path.items():
            path_to_ids.setdefault(path, []).append(tid)

        stale_ids: set[str] = set()
        for path, ids in path_to_ids.items():
            if len(ids) > 1:
                candidate_stale = ids[:-1]  # all but the last
                # Prefer removing non-cached first; only include cached if necessary
                non_cached_stale = [t for t in candidate_stale if not self._is_cached(id_to_msg_index.get(t, -1))]
                if non_cached_stale:
                    stale_ids.update(non_cached_stale)
                else:
                    # Fallback: remove cached stale reads when no non-cached option exists
                    stale_ids.update(candidate_stale)

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

        Cache-aware: collapse non-cached messages first.  Cached messages in
        the old section are passed through as-is; only when there are no
        non-cached messages to collapse do we fall back to collapsing cached ones.
        """
        if len(session.messages) <= keep_recent:
            return session

        old_messages = session.messages[:-keep_recent]
        recent_messages = session.messages[-keep_recent:]

        # Separate old messages into non-cached (collapse) and cached (preserve when possible)
        non_cached_old: list[tuple[int, Message]] = []
        cached_old: list[tuple[int, Message]] = []
        for rel_idx, msg in enumerate(old_messages):
            abs_idx = rel_idx  # old_messages starts at index 0
            if self._is_cached(abs_idx):
                cached_old.append((abs_idx, msg))
            else:
                non_cached_old.append((abs_idx, msg))

        # Collapse non-cached old messages into summary lines
        summary_lines: list[str] = []
        for _idx, msg in non_cached_old:
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    args_summary = ", ".join(
                        f"{k}={v!r}" for k, v in list(block.input.items())[:3]
                    )
                    summary_lines.append(f"Used {block.name}({args_summary})")
                elif isinstance(block, ToolResultBlock):
                    pass
                elif isinstance(block, TextBlock) and block.text.strip():
                    excerpt = block.text[:80].replace("\n", " ")
                    summary_lines.append(f"[msg] {excerpt}")

        # If non-cached messages produced summary lines, keep cached old messages intact
        if summary_lines or cached_old:
            # Build the new old section: cached messages preserved + summary of non-cached
            preserved_cached = tuple(msg for _idx, msg in cached_old)
            if summary_lines:
                summary_text = "\n".join(summary_lines)
                summary_msg = Message(
                    role="user",
                    content=(TextBlock(text=f"[Context summary]\n{summary_text}"),),
                )
                new_old_section = preserved_cached + (summary_msg,)
            else:
                new_old_section = preserved_cached

            if not new_old_section:
                return dataclasses.replace(session, messages=recent_messages)
            return dataclasses.replace(
                session,
                messages=new_old_section + recent_messages,
            )

        # Fallback: collapse all old messages (including cached) — no non-cached existed
        all_summary_lines: list[str] = []
        for msg in old_messages:
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    args_summary = ", ".join(
                        f"{k}={v!r}" for k, v in list(block.input.items())[:3]
                    )
                    all_summary_lines.append(f"Used {block.name}({args_summary})")
                elif isinstance(block, ToolResultBlock):
                    pass
                elif isinstance(block, TextBlock) and block.text.strip():
                    excerpt = block.text[:80].replace("\n", " ")
                    all_summary_lines.append(f"[msg] {excerpt}")

        if not all_summary_lines:
            return dataclasses.replace(session, messages=recent_messages)

        summary_text = "\n".join(all_summary_lines)
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

        Cache-aware: cached messages from the old section are preserved before
        the summary placeholder so they remain available for API cache hits.
        """
        if len(session.messages) <= keep_recent:
            return session

        old_messages = session.messages[:-keep_recent]
        recent = session.messages[-keep_recent:]

        # Preserve cached messages from the old section
        preserved_cached = tuple(
            msg for idx, msg in enumerate(old_messages) if self._is_cached(idx)
        )

        summary_msg = Message(
            role="user",
            content=(TextBlock(text="[Previous conversation summary]\n"),),
        )
        return dataclasses.replace(
            session,
            messages=preserved_cached + (summary_msg,) + recent,
        )
