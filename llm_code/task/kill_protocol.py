"""Task kill protocol + output store (H5b skeleton — Sprint 3).

Task types in Claude Code (local_bash, local_agent, remote_agent,
in_process_teammate, dream) each implement their own ``.kill()`` so
the runtime can cancel whichever concrete task type is in flight
uniformly. llm-code's current task layer keeps everything in-process
and doesn't distinguish; this module introduces:

    * :class:`TaskKiller` — runtime-checkable Protocol so adapters
      (local_bash runner, subagent handle, remote MCP tool, ...) can
      plug in without inheriting from a shared base.
    * :class:`KillResult` — structured outcome so the runtime can
      tell "actually terminated" from "target already gone".
    * :class:`TaskOutputStore` — file-backed log store. ``TaskState``
      keeps a ``{task_id}.log`` pointer + offset instead of storing
      full output inline, which matters once tasks produce megabytes
      of bash output.

Skeleton — task manager integration lands in a follow-up.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class KillResult:
    """Outcome of a :meth:`TaskKiller.kill` call."""
    killed: bool
    reason: str


@runtime_checkable
class TaskKiller(Protocol):
    """Adapter contract for killing a concrete task type."""
    task_type: str

    def kill(self, reason: str) -> KillResult: ...


class TaskOutputStore:
    """Per-task append-only log store backed by files.

    Each task id maps to ``{base_dir}/{task_id}.log``; :meth:`offset`
    returns the current file size so callers can tail incrementally
    without re-reading the whole log.

    Task ids are validated against path-traversal — ``..`` and path
    separators are rejected so untrusted ids can't write outside the
    store.
    """

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)

    # ── Path helpers ────────────────────────────────────────────────

    def _validate_id(self, task_id: str) -> None:
        if not task_id:
            raise ValueError("task_id must not be empty")
        if os.sep in task_id or "/" in task_id or task_id.startswith("."):
            raise ValueError(f"unsafe task_id: {task_id!r}")

    def path_for(self, task_id: str) -> Path:
        self._validate_id(task_id)
        return self._base_dir / f"{task_id}.log"

    # ── Append / read ────────────────────────────────────────────────

    def append(self, task_id: str, text: str) -> int:
        """Append ``text`` to the task log. Returns the new file size."""
        path = self.path_for(task_id)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(text)
        return path.stat().st_size

    def read(self, task_id: str) -> str:
        path = self.path_for(task_id)
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8")

    def tail(self, task_id: str, n: int) -> str:
        """Return the last ``n`` characters of the log (empty when
        the log doesn't exist — callers treat it as "no output yet")."""
        path = self.path_for(task_id)
        if not path.is_file():
            return ""
        content = path.read_text(encoding="utf-8")
        if n <= 0:
            return ""
        return content[-n:]

    def offset(self, task_id: str) -> int:
        path = self.path_for(task_id)
        if not path.is_file():
            return 0
        return path.stat().st_size

    def clear(self, task_id: str) -> None:
        path = self.path_for(task_id)
        if path.is_file():
            path.unlink()
