"""Multi-layer memory structure for llm-code."""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_code.runtime.memory import MemoryStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GovernanceRule:
    """A parsed governance rule from CLAUDE.md or .llm-code/rules/*.md."""

    category: str
    content: str
    source: str
    priority: int = 0


@dataclass(frozen=True)
class MemoryEntry:
    """A tagged, timestamped memory entry for L2 Project Memory."""

    key: str
    value: str
    tags: tuple[str, ...] = ()
    created_at: str = ""
    accessed_at: str = ""


@dataclass(frozen=True)
class TaskRecord:
    """A tracked task for L3 Task Memory."""

    task_id: str
    description: str
    status: str  # "incomplete" | "complete" | "blocked"
    created_at: str = ""
    updated_at: str = ""
    metadata: dict = field(default_factory=dict)


class GovernanceLayer:
    """L0: Scans CLAUDE.md, .llm-code/rules/*.md, .llm-code/governance.md."""

    _PRIORITY_MAP = {
        "governance.md": 10,
        "rules": 5,
        "CLAUDE.md": 1,
    }

    def __init__(self, project_root: Path) -> None:
        self._root = project_root

    def scan(self) -> tuple[GovernanceRule, ...]:
        """Scan all governance sources and return parsed rules."""
        rules: list[GovernanceRule] = []

        # 1. CLAUDE.md
        claude_md = self._root / "CLAUDE.md"
        if claude_md.is_file():
            rules.extend(self._parse_file(claude_md, priority=1))

        # 2. .llm-code/rules/*.md
        rules_dir = self._root / ".llm-code" / "rules"
        if rules_dir.is_dir():
            for md_file in sorted(rules_dir.glob("*.md")):
                rules.extend(self._parse_file(md_file, priority=5))

        # 3. .llm-code/governance.md
        gov_md = self._root / ".llm-code" / "governance.md"
        if gov_md.is_file():
            rules.extend(self._parse_file(gov_md, priority=10))

        return tuple(rules)

    def _parse_file(self, path: Path, priority: int) -> list[GovernanceRule]:
        """Parse a markdown file into GovernanceRule entries.

        Extracts list items (lines starting with '- ') as individual rules.
        Uses the most recent heading as the category.
        """
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return []

        parsed: list[GovernanceRule] = []
        category = "general"

        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                category = stripped.lstrip("#").strip().lower()
            elif stripped.startswith("- "):
                content = stripped[2:].strip()
                if content:
                    parsed.append(GovernanceRule(
                        category=category,
                        content=content,
                        source=str(path),
                        priority=priority,
                    ))

        return parsed


class WorkingMemory:
    """L1: In-memory session-scoped key-value store. Not persisted."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def store(self, key: str, value: str) -> None:
        self._data[key] = value

    def recall(self, key: str) -> str | None:
        return self._data.get(key)

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def list_keys(self) -> list[str]:
        return list(self._data.keys())

    def get_all(self) -> dict[str, str]:
        return dict(self._data)

    def clear(self) -> None:
        self._data.clear()


class ProjectMemory:
    """L2: Persistent project-scoped memory with tags. Wraps MemoryStore."""

    def __init__(self, memory_dir: Path, project_path: Path) -> None:
        from llm_code.runtime.memory import MemoryStore

        self._memory_store = MemoryStore(memory_dir, project_path)
        self._tags_file = self._memory_store._dir / "tags.json"

    @property
    def memory_store(self) -> "MemoryStore":
        """Expose underlying MemoryStore for backward compatibility."""
        return self._memory_store

    def store(self, key: str, value: str, tags: tuple[str, ...] = ()) -> None:
        """Store a value with optional tags."""
        self._memory_store.store(key, value)
        now = datetime.now(timezone.utc).isoformat()
        tags_data = self._load_tags()
        tags_data[key] = {
            "tags": list(tags),
            "created_at": tags_data.get(key, {}).get("created_at", now),
            "accessed_at": now,
        }
        self._save_tags(tags_data)

    def recall(self, key: str) -> MemoryEntry | None:
        """Return a MemoryEntry for the key, or None."""
        raw_value = self._memory_store.recall(key)
        if raw_value is None:
            return None
        tags_data = self._load_tags()
        meta = tags_data.get(key, {})
        now = datetime.now(timezone.utc).isoformat()
        # Update accessed_at
        if key in tags_data:
            tags_data[key]["accessed_at"] = now
            self._save_tags(tags_data)
        return MemoryEntry(
            key=key,
            value=raw_value,
            tags=tuple(meta.get("tags", [])),
            created_at=meta.get("created_at", ""),
            accessed_at=now,
        )

    def query_by_tag(self, tag: str) -> tuple[MemoryEntry, ...]:
        """Return all entries matching the given tag."""
        tags_data = self._load_tags()
        results: list[MemoryEntry] = []
        for key, meta in tags_data.items():
            if tag in meta.get("tags", []):
                raw_value = self._memory_store.recall(key)
                if raw_value is not None:
                    results.append(MemoryEntry(
                        key=key,
                        value=raw_value,
                        tags=tuple(meta.get("tags", [])),
                        created_at=meta.get("created_at", ""),
                        accessed_at=meta.get("accessed_at", ""),
                    ))
        return tuple(results)

    def delete(self, key: str) -> None:
        self._memory_store.delete(key)
        tags_data = self._load_tags()
        tags_data.pop(key, None)
        self._save_tags(tags_data)

    def list_keys(self) -> list[str]:
        return self._memory_store.list_keys()

    def get_all(self) -> dict[str, MemoryEntry]:
        tags_data = self._load_tags()
        result: dict[str, MemoryEntry] = {}
        for key, raw_entry in self._memory_store.get_all().items():
            meta = tags_data.get(key, {})
            result[key] = MemoryEntry(
                key=key,
                value=raw_entry.value,
                tags=tuple(meta.get("tags", [])),
                created_at=meta.get("created_at", raw_entry.created_at),
                accessed_at=meta.get("accessed_at", raw_entry.updated_at),
            )
        return result

    def _load_tags(self) -> dict:
        if self._tags_file.exists():
            try:
                return json.loads(self._tags_file.read_text())
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_tags(self, data: dict) -> None:
        self._tags_file.write_text(json.dumps(data, indent=2))


class TaskMemory:
    """L3: Per-task JSON files with status tracking."""

    def __init__(self, memory_dir: Path, project_path: Path) -> None:
        project_hash = hashlib.sha256(str(project_path).encode()).hexdigest()[:8]
        self._tasks_dir = memory_dir / project_hash / "tasks"
        self._tasks_dir.mkdir(parents=True, exist_ok=True)

    def create(
        self, description: str, metadata: dict | None = None,
    ) -> TaskRecord:
        """Create a new incomplete task and persist it."""
        import uuid

        now = datetime.now(timezone.utc).isoformat()
        task_id = uuid.uuid4().hex[:8]
        task = TaskRecord(
            task_id=task_id,
            description=description,
            status="incomplete",
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )
        self._save_task(task)
        return task

    def get(self, task_id: str) -> TaskRecord | None:
        """Load a task by ID, or None if not found."""
        path = self._tasks_dir / f"{task_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return TaskRecord(
                task_id=data["task_id"],
                description=data["description"],
                status=data["status"],
                created_at=data.get("created_at", ""),
                updated_at=data.get("updated_at", ""),
                metadata=data.get("metadata", {}),
            )
        except (json.JSONDecodeError, KeyError, OSError):
            return None

    def update_status(self, task_id: str, status: str) -> TaskRecord | None:
        """Update a task's status and return the new record."""
        task = self.get(task_id)
        if task is None:
            return None
        now = datetime.now(timezone.utc).isoformat()
        updated = TaskRecord(
            task_id=task.task_id,
            description=task.description,
            status=status,
            created_at=task.created_at,
            updated_at=now,
            metadata=task.metadata,
        )
        self._save_task(updated)
        return updated

    def list_incomplete(self) -> tuple[TaskRecord, ...]:
        """Scan all task files and return those with status 'incomplete'."""
        results: list[TaskRecord] = []
        for path in self._tasks_dir.glob("*.json"):
            task_id = path.stem
            task = self.get(task_id)
            if task is not None and task.status == "incomplete":
                results.append(task)
        return tuple(results)

    def delete(self, task_id: str) -> None:
        """Remove a task file."""
        path = self._tasks_dir / f"{task_id}.json"
        if path.exists():
            path.unlink()

    def _save_task(self, task: TaskRecord) -> None:
        data = {
            "task_id": task.task_id,
            "description": task.description,
            "status": task.status,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "metadata": task.metadata,
        }
        path = self._tasks_dir / f"{task.task_id}.json"
        path.write_text(json.dumps(data, indent=2))


class LayeredMemory:
    """Facade wrapping all 4 memory layers.

    - L0 Governance: parsed rules from CLAUDE.md / .llm-code/rules/ / governance.md
    - L1 Working: in-memory, session-scoped, not persisted
    - L2 Project: persistent, tag-based (wraps MemoryStore for backward compat)
    - L3 Task: per-task JSON files with status tracking
    """

    def __init__(
        self,
        project_root: Path,
        memory_dir: Path,
        project_path: Path,
    ) -> None:
        self._governance = GovernanceLayer(project_root)
        self._working = WorkingMemory()
        self._project = ProjectMemory(memory_dir, project_path)
        self._tasks = TaskMemory(memory_dir, project_path)

    @property
    def governance(self) -> GovernanceLayer:
        return self._governance

    @property
    def working(self) -> WorkingMemory:
        return self._working

    @property
    def project(self) -> ProjectMemory:
        return self._project

    @property
    def tasks(self) -> TaskMemory:
        return self._tasks

    def get_governance_rules(self) -> tuple[GovernanceRule, ...]:
        """Return all governance rules, sorted by priority descending."""
        rules = self._governance.scan()
        return tuple(sorted(rules, key=lambda r: r.priority, reverse=True))

    def get_incomplete_tasks(self) -> tuple[TaskRecord, ...]:
        """Scan for incomplete tasks (useful on startup)."""
        return self._tasks.list_incomplete()
