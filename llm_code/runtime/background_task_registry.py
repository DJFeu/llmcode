"""Registry for tracking in-flight background asyncio tasks.

Used by the TUI to display a "N bg" indicator and to cancel any pending
background tool/agent work during graceful shutdown.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AsyncTaskInfo:
    """Immutable snapshot of a registered asyncio task."""

    task_id: str
    title: str
    started_at: float


class AsyncTaskRegistry:
    """Thread-safe registry of in-flight asyncio.Tasks."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks: dict[str, tuple[AsyncTaskInfo, "asyncio.Task[object]"]] = {}

    def register(self, task: "asyncio.Task[object]", title: str) -> str:
        """Register an asyncio.Task. Auto-unregisters on completion."""
        task_id = uuid.uuid4().hex[:12]
        info = AsyncTaskInfo(task_id=task_id, title=title, started_at=time.time())
        with self._lock:
            self._tasks[task_id] = (info, task)
        logger.debug("AsyncTaskRegistry: registered %s title=%s", task_id, title)

        def _on_done(_t: "asyncio.Task[object]", tid: str = task_id) -> None:
            self.unregister(tid)

        task.add_done_callback(_on_done)
        return task_id

    def unregister(self, task_id: str) -> Optional[AsyncTaskInfo]:
        """Remove a task by id. Returns its info if present."""
        with self._lock:
            entry = self._tasks.pop(task_id, None)
        if entry is not None:
            logger.debug("AsyncTaskRegistry: unregistered %s", task_id)
            return entry[0]
        return None

    def list_active(self) -> list[AsyncTaskInfo]:
        """Return active (not-yet-done) tasks; prunes completed ones."""
        with self._lock:
            dead: list[str] = []
            alive: list[AsyncTaskInfo] = []
            for tid, (info, task) in self._tasks.items():
                if task.done():
                    dead.append(tid)
                else:
                    alive.append(info)
            for tid in dead:
                self._tasks.pop(tid, None)
        return alive

    def active_count(self) -> int:
        """Cheap count of currently in-flight registered tasks."""
        return len(self.list_active())

    async def cancel_all(self, timeout: float = 2.0) -> list[AsyncTaskInfo]:
        """Cancel all pending tasks and await completion within ``timeout``.

        Returns the snapshot of tasks targeted (pre-cancel).
        """
        with self._lock:
            entries = list(self._tasks.values())

        if not entries:
            return []

        snapshot = [info for info, _t in entries]
        pending: list["asyncio.Task[object]"] = []
        for _info, task in entries:
            if not task.done():
                task.cancel()
                pending.append(task)

        if pending:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*pending, return_exceptions=True),
                    timeout=max(0.0, timeout),
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "AsyncTaskRegistry: %d task(s) did not finish within %.1fs",
                    len(pending),
                    timeout,
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("AsyncTaskRegistry: cancel_all error: %s", exc)

        with self._lock:
            for info in snapshot:
                self._tasks.pop(info.task_id, None)
        return snapshot


_global_async_registry: Optional[AsyncTaskRegistry] = None
_global_async_lock = threading.Lock()


def global_async_registry() -> AsyncTaskRegistry:
    """Return the process-wide :class:`AsyncTaskRegistry` singleton."""
    global _global_async_registry
    with _global_async_lock:
        if _global_async_registry is None:
            _global_async_registry = AsyncTaskRegistry()
        return _global_async_registry
