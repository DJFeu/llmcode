"""Task lifecycle manager with state machine transitions and disk persistence."""
from __future__ import annotations

import dataclasses
import json
import uuid
from pathlib import Path

from llm_code.task.types import TaskState, TaskStatus, VerifyResult, _now_iso


# Valid transitions: from_status -> set of allowed to_statuses
_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PLAN: frozenset({TaskStatus.DO, TaskStatus.BLOCKED}),
    TaskStatus.DO: frozenset({TaskStatus.VERIFY, TaskStatus.BLOCKED}),
    TaskStatus.VERIFY: frozenset({TaskStatus.CLOSE, TaskStatus.DO, TaskStatus.BLOCKED}),
    TaskStatus.CLOSE: frozenset({TaskStatus.DONE, TaskStatus.BLOCKED}),
    TaskStatus.DONE: frozenset(),  # terminal
    TaskStatus.BLOCKED: frozenset({TaskStatus.PLAN, TaskStatus.DO, TaskStatus.VERIFY}),
}


class TaskLifecycleManager:
    """Manages task creation, state transitions, and persistence."""

    def __init__(self, task_dir: Path) -> None:
        self._task_dir = task_dir
        self._task_dir.mkdir(parents=True, exist_ok=True)

    def create_task(
        self,
        title: str,
        plan: str = "",
        goals: tuple[str, ...] = (),
        session_id: str = "",
    ) -> TaskState:
        """Create a new task in PLAN status and persist to disk."""
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        now = _now_iso()
        task = TaskState(
            id=task_id,
            title=title,
            status=TaskStatus.PLAN,
            plan=plan,
            goals=goals,
            created_at=now,
            updated_at=now,
            session_id=session_id,
        )
        self._save(task)
        return task

    def transition(self, task_id: str, to_status: TaskStatus) -> TaskState:
        """Transition a task to a new status, validating the state machine."""
        task = self._load(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")

        allowed = _TRANSITIONS.get(task.status, frozenset())
        if to_status not in allowed:
            raise ValueError(
                f"Invalid transition: {task.status.value} -> {to_status.value}. "
                f"Allowed: {', '.join(s.value for s in allowed)}"
            )

        updated = dataclasses.replace(task, status=to_status, updated_at=_now_iso())
        self._save(updated)
        return updated

    def get_task(self, task_id: str) -> TaskState | None:
        """Get a task by ID, or None if not found."""
        return self._load(task_id)

    def list_tasks(
        self,
        status: TaskStatus | None = None,
        exclude_done: bool = False,
    ) -> tuple[TaskState, ...]:
        """List all tasks, optionally filtered by status."""
        tasks: list[TaskState] = []
        for path in sorted(self._task_dir.glob("task-*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                task = TaskState.from_dict(data)
                if status is not None and task.status != status:
                    continue
                if exclude_done and task.status == TaskStatus.DONE:
                    continue
                tasks.append(task)
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
        return tuple(tasks)

    def running_task_count(self) -> int:
        """Count tasks in active states (PLAN, DO, VERIFY)."""
        active = {TaskStatus.PLAN, TaskStatus.DO, TaskStatus.VERIFY}
        return sum(1 for t in self.list_tasks() if t.status in active)

    def update_task(self, task_id: str, **kwargs) -> TaskState:
        """Update arbitrary fields on a task (immutable replace)."""
        task = self._load(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")
        # Convert list values to tuples for frozen dataclass compatibility
        clean_kwargs: dict = {}
        for k, v in kwargs.items():
            if isinstance(v, list):
                clean_kwargs[k] = tuple(v)
            else:
                clean_kwargs[k] = v
        clean_kwargs["updated_at"] = _now_iso()
        updated = dataclasses.replace(task, **clean_kwargs)
        self._save(updated)
        return updated

    def append_verify_result(self, task_id: str, result: VerifyResult) -> TaskState:
        """Append a VerifyResult to a task's verify_results tuple."""
        task = self._load(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")
        updated = dataclasses.replace(
            task,
            verify_results=task.verify_results + (result,),
            updated_at=_now_iso(),
        )
        self._save(updated)
        return updated

    def _save(self, task: TaskState) -> None:
        path = self._task_dir / f"{task.id}.json"
        path.write_text(json.dumps(task.to_dict(), indent=2), encoding="utf-8")

    def _load(self, task_id: str) -> TaskState | None:
        path = self._task_dir / f"{task_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return TaskState.from_dict(data)
        except (json.JSONDecodeError, KeyError, ValueError):
            return None


def build_incomplete_tasks_prompt(manager: TaskLifecycleManager) -> str:
    """Build a system prompt section listing incomplete tasks from prior sessions."""
    tasks = manager.list_tasks(exclude_done=True)
    if not tasks:
        return ""

    lines = [
        "## Incomplete Tasks (from prior sessions)",
        "",
        "The following tasks are still in progress. Resume or address them:",
        "",
    ]
    for task in tasks:
        lines.append(f"- **{task.id}** [{task.status.value}]: {task.title}")
        if task.plan:
            plan_preview = task.plan[:200].replace("\n", " ")
            lines.append(f"  Plan: {plan_preview}")
        if task.goals:
            lines.append(f"  Goals: {', '.join(task.goals)}")
        if task.files_modified:
            lines.append(f"  Files: {', '.join(task.files_modified)}")
        lines.append("")

    return "\n".join(lines)
