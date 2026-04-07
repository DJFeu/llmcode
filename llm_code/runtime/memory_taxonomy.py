"""4-type memory taxonomy: user, feedback, project, reference.

File-based storage with MEMORY.md index and YAML frontmatter topic files.
Compatible with Claude Code's memdir architecture.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_MAX_FILE_BYTES = 25_600  # 25 KB
_MAX_INDEX_LINES = 200
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)


class MemoryFileTooLargeError(ValueError):
    """Raised when a memory entry serialization exceeds the per-file byte cap.

    Subclasses ``ValueError`` for backward compatibility with older callers
    that catch ``ValueError`` from :class:`TypedMemoryStore.store`/``update``.
    """

    def __init__(self, slug: str, size: int, limit: int = _MAX_FILE_BYTES) -> None:
        super().__init__(
            f"Memory file {slug!r} would be {size} bytes, exceeds limit {limit}"
        )
        self.slug = slug
        self.size = size
        self.limit = limit


class MemoryType(Enum):
    USER = "user"
    FEEDBACK = "feedback"
    PROJECT = "project"
    REFERENCE = "reference"


@dataclass(frozen=True)
class TypedMemoryEntry:
    """A single memory entry with type classification."""

    slug: str
    name: str
    description: str
    memory_type: MemoryType
    content: str
    created_at: str
    updated_at: str

    def to_frontmatter_md(self) -> str:
        """Serialize to YAML frontmatter + content markdown."""
        meta = {
            "name": self.name,
            "description": self.description,
            "type": self.memory_type.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        front = yaml.dump(meta, default_flow_style=False, allow_unicode=True).strip()
        return f"---\n{front}\n---\n\n{self.content}\n"

    @staticmethod
    def from_file(path: Path) -> TypedMemoryEntry:
        """Parse a topic file with YAML frontmatter.

        Backward compat: if ``created_at`` is missing from frontmatter
        (legacy entries), backfill it from the file's mtime as a
        one-time fallback. The next call to :meth:`TypedMemoryStore.write`
        will persist the backfill since ``write`` preserves any non-empty
        ``created_at``.
        """
        raw = path.read_text(encoding="utf-8")
        m = _FRONTMATTER_RE.match(raw)
        if not m:
            raise ValueError(f"Invalid memory file format: {path}")
        meta = yaml.safe_load(m.group(1)) or {}
        content = m.group(2).strip()
        created_raw = meta.get("created_at", "")
        if not created_raw:
            try:
                mtime = path.stat().st_mtime
                created_raw = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
            except OSError:
                created_raw = ""
        return TypedMemoryEntry(
            slug=path.stem,
            name=str(meta.get("name", path.stem)),
            description=str(meta.get("description", "")),
            memory_type=MemoryType(meta.get("type", "project")),
            content=content,
            created_at=str(created_raw),
            updated_at=str(meta.get("updated_at", "")),
        )


class TypedMemoryStore:
    """File-based 4-type memory store with MEMORY.md index.

    Layout:
        <memory_dir>/
            MEMORY.md          — curated index (<200 lines)
            topics/
                <slug>.md      — individual topic files with YAML frontmatter
    """

    def __init__(self, memory_dir: Path) -> None:
        self._dir = memory_dir
        self._topics_dir = memory_dir / "topics"
        self._index_path = memory_dir / "MEMORY.md"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._topics_dir.mkdir(exist_ok=True)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        slug: str,
        name: str,
        description: str,
        memory_type: MemoryType,
        content: str,
    ) -> TypedMemoryEntry:
        """Create a new memory entry. Raises if slug already exists."""
        from llm_code.runtime.memory_validator import validate_content

        path = self._topics_dir / f"{slug}.md"
        if path.exists():
            raise FileExistsError(f"Memory entry already exists: {slug}")

        valid, reason = validate_content(content, memory_type)
        if not valid:
            raise ValueError(f"Content rejected: {reason}")

        now = datetime.now(timezone.utc).isoformat()
        entry = TypedMemoryEntry(
            slug=slug,
            name=name,
            description=description,
            memory_type=memory_type,
            content=content,
            created_at=now,
            updated_at=now,
        )
        md = entry.to_frontmatter_md()
        if len(md.encode("utf-8")) > _MAX_FILE_BYTES:
            raise ValueError(f"Memory file exceeds {_MAX_FILE_BYTES} bytes limit")
        path.write_text(md, encoding="utf-8")
        self._rebuild_index()
        return entry

    def update(self, slug: str, content: str | None = None, description: str | None = None) -> TypedMemoryEntry:
        """Update an existing memory entry."""
        from llm_code.runtime.memory_validator import validate_content

        path = self._topics_dir / f"{slug}.md"
        if not path.exists():
            raise FileNotFoundError(f"Memory entry not found: {slug}")

        entry = TypedMemoryEntry.from_file(path)
        new_content = content if content is not None else entry.content
        new_desc = description if description is not None else entry.description

        if content is not None:
            valid, reason = validate_content(new_content, entry.memory_type)
            if not valid:
                raise ValueError(f"Content rejected: {reason}")

        now = datetime.now(timezone.utc).isoformat()
        updated = TypedMemoryEntry(
            slug=slug,
            name=entry.name,
            description=new_desc,
            memory_type=entry.memory_type,
            content=new_content,
            created_at=entry.created_at,
            updated_at=now,
        )
        md = updated.to_frontmatter_md()
        if len(md.encode("utf-8")) > _MAX_FILE_BYTES:
            raise ValueError(f"Memory file exceeds {_MAX_FILE_BYTES} bytes limit")
        path.write_text(md, encoding="utf-8")
        self._rebuild_index()
        return updated

    def delete(self, slug: str) -> None:
        """Delete a memory entry."""
        path = self._topics_dir / f"{slug}.md"
        if path.exists():
            path.unlink()
        self._rebuild_index()

    def write(self, entry: TypedMemoryEntry) -> TypedMemoryEntry:
        """Create or overwrite a memory entry on disk (upsert).

        Enforces the 25KB per-file cap by raising
        :class:`MemoryFileTooLargeError`. Preserves ``created_at`` if a
        file with the same slug already exists; always bumps ``updated_at``
        to now. The index is rebuilt on success.
        """
        path = self._topics_dir / f"{entry.slug}.md"
        now = datetime.now(timezone.utc).isoformat()
        created_at = entry.created_at or now
        if path.exists():
            try:
                existing = TypedMemoryEntry.from_file(path)
                created_at = existing.created_at or created_at
            except ValueError:
                pass
        materialized = TypedMemoryEntry(
            slug=entry.slug,
            name=entry.name,
            description=entry.description,
            memory_type=entry.memory_type,
            content=entry.content,
            created_at=created_at,
            updated_at=now,
        )
        md = materialized.to_frontmatter_md()
        size = len(md.encode("utf-8"))
        if size > _MAX_FILE_BYTES:
            raise MemoryFileTooLargeError(entry.slug, size)
        path.write_text(md, encoding="utf-8")
        self._rebuild_index()
        return materialized

    def get(self, slug: str) -> TypedMemoryEntry | None:
        """Retrieve a single entry by slug."""
        path = self._topics_dir / f"{slug}.md"
        if not path.exists():
            return None
        return TypedMemoryEntry.from_file(path)

    def list_all(self) -> list[TypedMemoryEntry]:
        """Return all entries sorted by updated_at descending."""
        entries = []
        for path in sorted(self._topics_dir.glob("*.md")):
            try:
                entries.append(TypedMemoryEntry.from_file(path))
            except (ValueError, OSError):
                continue
        entries.sort(key=lambda e: e.updated_at, reverse=True)
        return entries

    def list_by_type(self, memory_type: MemoryType) -> list[TypedMemoryEntry]:
        """Return entries filtered by type."""
        return [e for e in self.list_all() if e.memory_type == memory_type]

    def search(self, query: str) -> list[TypedMemoryEntry]:
        """Search entries by keyword match, ranked by decayed recency.

        Each match starts with a base score of 1.0, then is multiplied by
        a type-specific exponential decay (see :mod:`memory_decay`). This
        keeps recent matches above older identical matches and lets fast-
        moving PROJECT entries fall behind slow-decaying USER/FEEDBACK
        entries of similar age.
        """
        from llm_code.runtime.memory_decay import apply_decay

        query_lower = query.lower()
        scored: list[tuple[float, TypedMemoryEntry]] = []
        for entry in self.list_all():
            searchable = f"{entry.name} {entry.description} {entry.content}".lower()
            if query_lower not in searchable:
                continue
            score = apply_decay(1.0, entry.memory_type, entry.created_at)
            scored.append((score, entry))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [entry for _, entry in scored]

    # ------------------------------------------------------------------
    # Index management
    # ------------------------------------------------------------------

    def _rebuild_index(self) -> None:
        """Rebuild MEMORY.md index from topic files."""
        entries = self.list_all()
        lines = ["# Memory Index\n"]

        by_type: dict[MemoryType, list[TypedMemoryEntry]] = {}
        for entry in entries:
            by_type.setdefault(entry.memory_type, []).append(entry)

        for mt in MemoryType:
            typed_entries = by_type.get(mt, [])
            if not typed_entries:
                continue
            lines.append(f"\n## {mt.value.title()}\n")
            for entry in typed_entries:
                desc_short = entry.description[:100] if entry.description else ""
                lines.append(f"- [{entry.name}](topics/{entry.slug}.md) — {desc_short}")

        # Truncate to max lines
        content = "\n".join(lines[:_MAX_INDEX_LINES])
        if len(lines) > _MAX_INDEX_LINES:
            content += f"\n\n_({len(lines) - _MAX_INDEX_LINES} entries truncated)_"
        self._index_path.write_text(content + "\n", encoding="utf-8")

    def get_index(self) -> str:
        """Return the MEMORY.md index content."""
        if self._index_path.exists():
            return self._index_path.read_text(encoding="utf-8")
        return ""

    # ------------------------------------------------------------------
    # Migration from legacy MemoryStore
    # ------------------------------------------------------------------

    def migrate_from_legacy(self, legacy_file: Path) -> int:
        """Import entries from a legacy memory.json file.

        Returns the number of entries migrated.
        """
        import json

        if not legacy_file.exists():
            return 0

        try:
            data = json.loads(legacy_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return 0

        count = 0
        for key, val in data.items():
            if key.startswith("_"):
                continue
            slug = re.sub(r"[^a-z0-9_-]", "-", key.lower()).strip("-")[:50]
            if not slug:
                continue
            value = val["value"] if isinstance(val, dict) else str(val)
            path = self._topics_dir / f"{slug}.md"
            if path.exists():
                continue
            try:
                self.create(
                    slug=slug,
                    name=key,
                    description=f"Migrated from legacy memory: {key}",
                    memory_type=MemoryType.PROJECT,
                    content=value,
                )
                count += 1
            except (ValueError, FileExistsError):
                continue

        # Backup legacy file
        backup = legacy_file.with_suffix(".json.bak")
        if not backup.exists():
            legacy_file.rename(backup)

        return count
