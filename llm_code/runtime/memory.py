"""Cross-session memory: MemoryStore for persistent key-value memory with session summaries."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class MemoryEntry:
    key: str
    value: str
    created_at: str
    updated_at: str


class MemoryStore:
    """Persistent key-value memory store scoped to a project path."""

    def __init__(self, memory_dir: Path, project_path: Path) -> None:
        project_hash = hashlib.sha256(str(project_path).encode()).hexdigest()[:8]
        self._dir = memory_dir / project_hash
        self._dir.mkdir(parents=True, exist_ok=True)
        self._memory_file = self._dir / "memory.json"
        self._sessions_dir = self._dir / "sessions"
        self._sessions_dir.mkdir(exist_ok=True)

    def store(self, key: str, value: str) -> None:
        """Store or update a key-value pair."""
        data = self._load()
        now = datetime.now(timezone.utc).isoformat()
        if key in data:
            data[key]["value"] = value
            data[key]["updated_at"] = now
        else:
            data[key] = {"value": value, "created_at": now, "updated_at": now}
        self._save(data)

    def recall(self, key: str) -> str | None:
        """Return the value for key, or None if not found."""
        data = self._load()
        entry = data.get(key)
        return entry["value"] if entry else None

    def list_keys(self) -> list[str]:
        """Return all stored keys."""
        return list(self._load().keys())

    def delete(self, key: str) -> None:
        """Remove a key from memory (no-op if key does not exist)."""
        data = self._load()
        data.pop(key, None)
        self._save(data)

    def list_entries(self) -> dict[str, str] | None:
        """Return a dict mapping key → value for all stored entries, or None if empty.

        This is used by the prompt builder to inject memory into the system prompt.
        Internal keys (starting with '_') are excluded.
        """
        data = self._load()
        entries = {k: v["value"] for k, v in data.items() if not k.startswith("_")}
        return entries if entries else None

    def get_all(self) -> dict[str, MemoryEntry]:
        """Return all entries as a mapping of key -> MemoryEntry."""
        data = self._load()
        return {
            k: MemoryEntry(
                key=k,
                value=v["value"],
                created_at=v["created_at"],
                updated_at=v["updated_at"],
            )
            for k, v in data.items()
        }

    def save_session_summary(self, summary: str) -> None:
        """Persist a session summary as a timestamped Markdown file."""
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        path = self._sessions_dir / f"{timestamp}.md"
        path.write_text(summary, encoding="utf-8")

    def load_recent_summaries(self, limit: int = 5) -> list[str]:
        """Return the most recent session summaries (newest first)."""
        files = sorted(self._sessions_dir.glob("*.md"), reverse=True)[:limit]
        return [f.read_text(encoding="utf-8") for f in files]

    @property
    def consolidated_dir(self) -> Path:
        """Return the consolidated summaries directory, creating it if needed."""
        d = self._dir / "consolidated"
        d.mkdir(exist_ok=True)
        return d

    def save_consolidated(self, content: str, date_str: str | None = None) -> Path:
        """Persist a consolidated summary as a dated Markdown file.

        Args:
            content: The markdown summary content.
            date_str: Optional date string (YYYY-MM-DD). Defaults to today (UTC).

        Returns:
            The path to the written file.
        """
        if date_str is None:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = self.consolidated_dir / f"{date_str}.md"
        path.write_text(content, encoding="utf-8")
        return path

    def load_consolidated_summaries(self, limit: int = 10) -> list[str]:
        """Return the most recent consolidated summaries (newest first)."""
        files = sorted(self.consolidated_dir.glob("*.md"), reverse=True)[:limit]
        return [f.read_text(encoding="utf-8") for f in files]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> dict:
        if self._memory_file.exists():
            try:
                return json.loads(self._memory_file.read_text())
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save(self, data: dict) -> None:
        self._memory_file.write_text(json.dumps(data, indent=2))
