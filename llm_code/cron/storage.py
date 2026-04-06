"""Persistent storage for scheduled cron tasks."""
from __future__ import annotations

import dataclasses
import datetime
import json
import uuid
from pathlib import Path

_MAX_TASKS = 50
_ISO_FORMAT = "%Y-%m-%dT%H:%M:%S"


@dataclasses.dataclass(frozen=True)
class CronTask:
    id: str
    cron: str
    prompt: str
    recurring: bool
    permanent: bool
    created_at: datetime.datetime
    last_fired_at: datetime.datetime | None = None


class CronStorage:
    """Load/save cron tasks from a JSON file under .llmcode/."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._tasks: list[CronTask] = self._load()

    def _load(self) -> list[CronTask]:
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        tasks: list[CronTask] = []
        for raw in data.get("tasks", []):
            last_fired = None
            if raw.get("last_fired_at"):
                last_fired = datetime.datetime.strptime(raw["last_fired_at"], _ISO_FORMAT)
            tasks.append(CronTask(
                id=raw["id"],
                cron=raw["cron"],
                prompt=raw["prompt"],
                recurring=raw.get("recurring", True),
                permanent=raw.get("permanent", False),
                created_at=datetime.datetime.strptime(raw["created_at"], _ISO_FORMAT),
                last_fired_at=last_fired,
            ))
        return tasks

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "tasks": [
                {
                    "id": t.id,
                    "cron": t.cron,
                    "prompt": t.prompt,
                    "recurring": t.recurring,
                    "permanent": t.permanent,
                    "created_at": t.created_at.strftime(_ISO_FORMAT),
                    "last_fired_at": t.last_fired_at.strftime(_ISO_FORMAT) if t.last_fired_at else None,
                }
                for t in self._tasks
            ]
        }
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def add(
        self,
        cron: str,
        prompt: str,
        recurring: bool,
        permanent: bool,
    ) -> CronTask:
        """Add a new task. Raises ValueError if at capacity (50)."""
        if len(self._tasks) >= _MAX_TASKS:
            raise ValueError(f"Maximum {_MAX_TASKS} scheduled tasks reached")
        task = CronTask(
            id=uuid.uuid4().hex[:12],
            cron=cron,
            prompt=prompt,
            recurring=recurring,
            permanent=permanent,
            created_at=datetime.datetime.now(),
        )
        self._tasks = [*self._tasks, task]
        self._save()
        return task

    def remove(self, task_id: str) -> bool:
        """Remove a task by ID. Returns True if found and removed."""
        new_tasks = [t for t in self._tasks if t.id != task_id]
        if len(new_tasks) == len(self._tasks):
            return False
        self._tasks = new_tasks
        self._save()
        return True

    def list_all(self) -> list[CronTask]:
        """Return all tasks (immutable copies via frozen dataclass)."""
        return list(self._tasks)

    def update_last_fired(
        self,
        task_id: str,
        fired_at: datetime.datetime,
    ) -> CronTask | None:
        """Update last_fired_at for a task. Returns updated task or None."""
        new_tasks: list[CronTask] = []
        updated: CronTask | None = None
        for t in self._tasks:
            if t.id == task_id:
                updated = dataclasses.replace(t, last_fired_at=fired_at)
                new_tasks.append(updated)
            else:
                new_tasks.append(t)
        if updated is None:
            return None
        self._tasks = new_tasks
        self._save()
        return updated
