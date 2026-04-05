"""Session management: immutable Session dataclass, SessionManager for persistence."""
from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from llm_code.api.types import (
    ContentBlock,
    ImageBlock,
    Message,
    TextBlock,
    TokenUsage,
    ToolResultBlock,
    ToolUseBlock,
)


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _block_to_dict(block: ContentBlock) -> dict:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ToolUseBlock):
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    if isinstance(block, ToolResultBlock):
        return {
            "type": "tool_result",
            "tool_use_id": block.tool_use_id,
            "content": block.content,
            "is_error": block.is_error,
        }
    if isinstance(block, ImageBlock):
        return {"type": "image", "media_type": block.media_type, "data": block.data}
    raise ValueError(f"Unknown block type: {type(block)}")


def _dict_to_block(d: dict) -> ContentBlock:
    t = d["type"]
    if t == "text":
        return TextBlock(text=d["text"])
    if t == "tool_use":
        return ToolUseBlock(id=d["id"], name=d["name"], input=d["input"])
    if t == "tool_result":
        return ToolResultBlock(
            tool_use_id=d["tool_use_id"],
            content=d["content"],
            is_error=d.get("is_error", False),
        )
    if t == "image":
        return ImageBlock(media_type=d["media_type"], data=d["data"])
    raise ValueError(f"Unknown block type: {t}")


def _message_to_dict(msg: Message) -> dict:
    return {
        "role": msg.role,
        "content": [_block_to_dict(b) for b in msg.content],
    }


def _dict_to_message(d: dict) -> Message:
    return Message(
        role=d["role"],
        content=tuple(_dict_to_block(b) for b in d["content"]),
    )


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Session:
    id: str
    messages: tuple[Message, ...]
    created_at: str
    updated_at: str
    total_usage: TokenUsage
    project_path: Path
    name: str = ""
    tags: tuple[str, ...] = ()

    @classmethod
    def create(cls, project_path: Path) -> "Session":
        """Create a new empty session with a unique 8-char hex ID."""
        now = datetime.now(timezone.utc).isoformat()
        session_id = uuid.uuid4().hex[:8]
        return cls(
            id=session_id,
            messages=(),
            created_at=now,
            updated_at=now,
            total_usage=TokenUsage(input_tokens=0, output_tokens=0),
            project_path=project_path,
        )

    def add_message(self, msg: Message) -> "Session":
        """Return a new Session with the message appended (immutable)."""
        now = datetime.now(timezone.utc).isoformat()
        return dataclasses.replace(
            self,
            messages=self.messages + (msg,),
            updated_at=now,
        )

    def rename(self, name: str) -> "Session":
        """Return a new Session with the given name (immutable)."""
        now = datetime.now(timezone.utc).isoformat()
        return dataclasses.replace(self, name=name, updated_at=now)

    def add_tags(self, *tags: str) -> "Session":
        """Return a new Session with tags merged (deduped, order-preserving, immutable)."""
        merged = tuple(dict.fromkeys(self.tags + tags))
        now = datetime.now(timezone.utc).isoformat()
        return dataclasses.replace(self, tags=merged, updated_at=now)

    def update_usage(self, usage: TokenUsage) -> "Session":
        """Return a new Session with accumulated token usage (immutable)."""
        now = datetime.now(timezone.utc).isoformat()
        new_usage = TokenUsage(
            input_tokens=self.total_usage.input_tokens + usage.input_tokens,
            output_tokens=self.total_usage.output_tokens + usage.output_tokens,
        )
        return dataclasses.replace(self, total_usage=new_usage, updated_at=now)

    def estimated_tokens(self) -> int:
        """Rough token estimate: total character count divided by 4."""
        char_count = 0
        for msg in self.messages:
            for block in msg.content:
                if isinstance(block, TextBlock):
                    char_count += len(block.text)
                elif isinstance(block, ToolResultBlock):
                    char_count += len(block.content)
                elif isinstance(block, ToolUseBlock):
                    char_count += len(block.name) + len(str(block.input))
        return char_count // 4

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "messages": [_message_to_dict(m) for m in self.messages],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "total_usage": {
                "input_tokens": self.total_usage.input_tokens,
                "output_tokens": self.total_usage.output_tokens,
            },
            "project_path": str(self.project_path),
            "name": self.name,
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        return cls(
            id=data["id"],
            messages=tuple(_dict_to_message(m) for m in data["messages"]),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            total_usage=TokenUsage(
                input_tokens=data["total_usage"]["input_tokens"],
                output_tokens=data["total_usage"]["output_tokens"],
            ),
            project_path=Path(data["project_path"]),
            name=data.get("name", ""),
            tags=tuple(data.get("tags", ())),
        )


# ---------------------------------------------------------------------------
# SessionSummary
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class SessionSummary:
    id: str
    project_path: Path
    created_at: str
    message_count: int
    name: str = ""
    tags: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------

class SessionManager:
    def __init__(self, session_dir: Path) -> None:
        self._session_dir = session_dir
        session_dir.mkdir(parents=True, exist_ok=True)

    def save(self, session: Session) -> Path:
        """Persist session as JSON; returns the file path."""
        path = self._session_dir / f"{session.id}.json"
        path.write_text(json.dumps(session.to_dict(), indent=2), encoding="utf-8")
        return path

    def load(self, session_id: str) -> Session:
        """Load session by ID; raises FileNotFoundError if missing."""
        path = self._session_dir / f"{session_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Session '{session_id}' not found at {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return Session.from_dict(data)

    def list_sessions(self) -> list[SessionSummary]:
        """Return session summaries sorted by modification time (most recent first)."""
        files = sorted(
            self._session_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        summaries: list[SessionSummary] = []
        for f in files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                summaries.append(
                    SessionSummary(
                        id=data["id"],
                        project_path=Path(data["project_path"]),
                        created_at=data["created_at"],
                        message_count=len(data["messages"]),
                        name=data.get("name", ""),
                        tags=tuple(data.get("tags", ())),
                    )
                )
            except (json.JSONDecodeError, KeyError):
                continue
        return summaries
