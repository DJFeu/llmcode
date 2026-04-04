"""Asyncio-based cron scheduler with file locking and auto-expiry."""
from __future__ import annotations

import asyncio
import datetime
import fcntl
import logging
from pathlib import Path
from typing import Awaitable, Callable

from llm_code.cron.parser import next_fire_time, parse_cron
from llm_code.cron.storage import CronStorage, CronTask

logger = logging.getLogger(__name__)

_EXPIRY_DAYS = 30
_DEFAULT_POLL_SECONDS = 60


class CronScheduler:
    """Background cron scheduler that polls storage every N seconds."""

    def __init__(
        self,
        storage: CronStorage,
        lock_path: Path,
        on_fire: Callable[[str], Awaitable[None]],
    ) -> None:
        self._storage = storage
        self._lock_path = Path(lock_path)
        self._on_fire = on_fire
        self._running = False

    async def start(self, poll_interval: float = _DEFAULT_POLL_SECONDS) -> None:
        """Run the scheduler loop until stop() is called."""
        self._running = True
        while self._running:
            try:
                await self._tick(now=datetime.datetime.now())
            except Exception:
                logger.exception("Error in cron scheduler tick")
            await asyncio.sleep(poll_interval)

    def stop(self) -> None:
        self._running = False

    def check_missed(self, now: datetime.datetime) -> list[CronTask]:
        """Return tasks that have missed fire times since their last_fired_at."""
        missed: list[CronTask] = []
        for task in self._storage.list_all():
            if task.last_fired_at is None:
                # Never fired — check if created_at means it should have fired
                ref = task.created_at
            else:
                ref = task.last_fired_at
            try:
                expr = parse_cron(task.cron)
                nxt = next_fire_time(expr, ref)
                if nxt <= now:
                    missed.append(task)
            except ValueError:
                continue
        return missed

    async def _tick(self, now: datetime.datetime) -> None:
        """Run one scheduling cycle."""
        if not self._try_lock():
            return

        try:
            await self._process_tasks(now)
        finally:
            self._release_lock()

    async def _process_tasks(self, now: datetime.datetime) -> None:
        tasks = self._storage.list_all()
        to_remove: list[str] = []

        for task in tasks:
            # Auto-expire non-permanent recurring tasks older than 30 days
            if not task.permanent and task.recurring:
                age = now - task.created_at
                if age.days > _EXPIRY_DAYS:
                    logger.info("Expiring task %s (age: %d days)", task.id, age.days)
                    to_remove.append(task.id)
                    continue

            # Determine if task should fire
            try:
                expr = parse_cron(task.cron)
            except ValueError:
                logger.warning("Invalid cron expression for task %s: %s", task.id, task.cron)
                continue

            ref = task.last_fired_at or task.created_at
            try:
                nxt = next_fire_time(expr, ref)
            except ValueError:
                continue

            if nxt <= now:
                logger.info("Firing task %s: %s", task.id, task.prompt)
                await self._on_fire(task.prompt)
                self._storage.update_last_fired(task.id, now)

                if not task.recurring:
                    to_remove.append(task.id)

        for tid in to_remove:
            self._storage.remove(tid)

    def _try_lock(self) -> bool:
        """Acquire a file lock; return True if successful."""
        try:
            self._lock_path.parent.mkdir(parents=True, exist_ok=True)
            # Close previous FD if exists to prevent leak
            if hasattr(self, "_lock_fd") and self._lock_fd:
                try:
                    self._lock_fd.close()
                except Exception:
                    pass
            self._lock_fd = open(self._lock_path, "w")
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (OSError, IOError):
            return False

    def _release_lock(self) -> None:
        """Release the file lock."""
        try:
            if hasattr(self, "_lock_fd") and self._lock_fd:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                self._lock_fd.close()
        except (OSError, IOError):
            pass
