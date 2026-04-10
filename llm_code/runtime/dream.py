"""DreamTask — background memory consolidation via LLM summarization."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
import typing
from typing import TYPE_CHECKING

from filelock import FileLock

from llm_code.api.types import Message, MessageRequest, TextBlock

if TYPE_CHECKING:
    from llm_code.api.provider import LLMProvider
    from llm_code.runtime.config import RuntimeConfig
    from llm_code.runtime.memory import MemoryStore
    from llm_code.runtime.session import Session

logger = logging.getLogger(__name__)

_CONSOLIDATION_SYSTEM_PROMPT = """\
You are a memory consolidation agent. Given a conversation transcript from a \
coding session, produce a structured Markdown summary with these sections:

## Summary
1-3 sentence overview of what was accomplished.

## Modified Files
Bulleted list of files that were created, edited, or deleted.

## Decisions
Key architectural or design decisions made during the session.

## Patterns
Reusable patterns, idioms, or techniques worth remembering for future sessions.

## Open Items
Any unfinished work, known issues, or next steps mentioned.

## Episodes
Extract 3-5 key episodes (significant events) as a JSON array. Each episode:
{"title": "short title", "type": "bug_fix|feature|decision|refactor|debug", \
"tags": ["tag1", "tag2"], "relates_to": ["related_concept1"]}

Example:
```json
[
  {"title": "Fix DuckDuckGo search backend", "type": "bug_fix", "tags": ["search", "ddg"], "relates_to": ["web_search"]},
  {"title": "Add proactive context compaction", "type": "feature", "tags": ["context", "compaction"], "relates_to": ["memory", "token_management"]}
]
```

Be concise. Focus on facts. Do not invent information not present in the transcript.
"""


class DreamTask:
    """Consolidates a session's conversation into a structured summary via LLM."""

    async def consolidate(
        self,
        session: "Session",
        memory_store: "MemoryStore",
        provider: "LLMProvider",
        config: "RuntimeConfig",
    ) -> str:
        """Run LLM-powered consolidation on the session.

        Returns the generated summary string, or empty string if skipped/failed.
        """
        dream_config = config.dream

        # Guard: disabled
        if not dream_config.enabled:
            return ""

        # Guard: too few messages
        user_messages = [m for m in session.messages if m.role == "user"]
        if len(user_messages) < dream_config.min_turns:
            return ""

        # Guard: time-based — skip if last run was < 24h ago
        last_run_str = memory_store.recall("_dream_last_run")
        if last_run_str:
            try:
                last_run = datetime.fromisoformat(last_run_str)
                hours_since = (datetime.now(timezone.utc) - last_run).total_seconds() / 3600
                if hours_since < 24:
                    logger.debug("DreamTask skipped: only %.1fh since last run", hours_since)
                    return ""
            except (ValueError, TypeError):
                pass  # proceed if unparseable

        # Guard: session count — skip if < 5 sessions since last consolidation
        session_count = memory_store.recall("_dream_session_count")
        count = int(session_count) if session_count and session_count.isdigit() else 0
        if count < 5:
            logger.debug("DreamTask skipped: only %d sessions (need 5)", count)
            return ""

        # Build transcript from session messages
        transcript_parts: list[str] = []
        for msg in session.messages:
            role_label = "User" if msg.role == "user" else "Assistant"
            for block in msg.content:
                if hasattr(block, "text"):
                    transcript_parts.append(f"**{role_label}:** {block.text}")

        transcript = "\n\n".join(transcript_parts)

        # Call LLM
        request = MessageRequest(
            model=getattr(config, "model", ""),
            messages=(
                Message(
                    role="user",
                    content=(TextBlock(text=f"Consolidate this session:\n\n{transcript}"),),
                ),
            ),
            system=_CONSOLIDATION_SYSTEM_PROMPT,
            tools=(),
            max_tokens=2048,
            temperature=0.3,
        )

        try:
            response = await provider.send_message(request)
        except Exception as exc:
            logger.warning("DreamTask consolidation failed: %s", exc)
            return ""

        # Extract text from response
        summary_parts: list[str] = []
        for block in response.content:
            if hasattr(block, "text"):
                summary_parts.append(block.text)
        summary = "\n".join(summary_parts)
        summary = self._normalize_dates(summary)

        if not summary.strip():
            return ""

        # Write with file lock
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        lock_path = memory_store.consolidated_dir / f"{today}.md.lock"
        lock = FileLock(str(lock_path), timeout=5)
        with lock:
            memory_store.save_consolidated(summary, date_str=today)

        # Extract episodes from summary and store as structured memory
        self._extract_episodes(summary, memory_store)

        # Update last-run timestamp
        memory_store.store(
            "_dream_last_run",
            datetime.now(timezone.utc).isoformat(),
        )

        # Reset session counter after successful consolidation
        memory_store.store("_dream_session_count", "0")

        # Prune MEMORY.md to stay within limits
        self._prune_memory_index(memory_store)

        logger.info(
            "DreamTask consolidated session to %s/%s.md",
            memory_store.consolidated_dir,
            today,
        )
        return summary

    @staticmethod
    def _normalize_dates(text: str) -> str:
        """Convert relative date references to absolute ISO dates."""
        today = datetime.now(timezone.utc)

        replacements: dict[str, str | typing.Callable[[re.Match[str]], str]] = {
            r"\byesterday\b": (today - timedelta(days=1)).strftime("%Y-%m-%d"),
            r"\btoday\b": today.strftime("%Y-%m-%d"),
            r"\b(\d+)\s+days?\s+ago\b": lambda m: (
                today - timedelta(days=int(m.group(1)))
            ).strftime("%Y-%m-%d"),
        }

        for pattern, replacement in replacements.items():
            if callable(replacement):
                text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
            else:
                text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

        return text

    @staticmethod
    def _prune_memory_index(memory_store: "MemoryStore") -> None:
        """Prune MEMORY.md to stay within 200 lines / 25KB."""
        index_path = memory_store._dir.parent / "MEMORY.md"
        if not index_path.exists():
            return

        content = index_path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines(keepends=True)

        _MAX_LINES = 200
        _MAX_BYTES = 25 * 1024  # 25KB

        changed = False

        # Prune by line count (keep first _MAX_LINES)
        if len(lines) > _MAX_LINES:
            logger.info("DreamTask prune: %d lines → %d", len(lines), _MAX_LINES)
            lines = lines[:_MAX_LINES]
            changed = True

        # Prune by byte size
        result = "".join(lines)
        if len(result.encode("utf-8")) > _MAX_BYTES:
            logger.info(
                "DreamTask prune: %d bytes → %d",
                len(result.encode("utf-8")),
                _MAX_BYTES,
            )
            while len("".join(lines).encode("utf-8")) > _MAX_BYTES and lines:
                lines.pop()
            changed = True

        if changed:
            index_path.write_text("".join(lines), encoding="utf-8")

    @staticmethod
    def increment_session_count(memory_store: "MemoryStore") -> None:
        """Increment the session counter for trigger logic."""
        current = memory_store.recall("_dream_session_count")
        count = int(current) if current and current.isdigit() else 0
        memory_store.store("_dream_session_count", str(count + 1))

    @staticmethod
    def _extract_episodes(
        summary: str, memory_store: "MemoryStore",
    ) -> None:
        """Extract episode JSON from summary and store as linked memory entries."""
        import json as _json
        import re

        # Find JSON array in ```json ... ``` block or bare [ ... ]
        match = re.search(r"```json\s*\n(\[.*?\])\s*\n```", summary, re.DOTALL)
        if not match:
            match = re.search(r"(\[\s*\{.*?\}\s*\])", summary, re.DOTALL)
        if not match:
            return

        try:
            episodes = _json.loads(match.group(1))
        except _json.JSONDecodeError:
            return

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for ep in episodes:
            if not isinstance(ep, dict) or "title" not in ep:
                continue
            key = f"episode:{today}:{ep['title'][:50]}"
            tags = tuple(ep.get("tags", ()))
            relates_to = tuple(ep.get("relates_to", ()))
            ep_type = ep.get("type", "")
            if ep_type:
                tags = tags + (ep_type,)
            memory_store.store(
                key=key,
                value=ep["title"],
                tags=tags,
                relates_to=relates_to,
            )
