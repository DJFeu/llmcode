"""DreamTask — background memory consolidation via LLM summarization."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
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

        logger.info(
            "DreamTask consolidated session to %s/%s.md",
            memory_store.consolidated_dir,
            today,
        )
        return summary

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
