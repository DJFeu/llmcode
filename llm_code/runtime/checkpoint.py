"""Git-based checkpoint manager for undoable tool operations."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class Checkpoint:
    id: str               # incrementing "cp-001", "cp-002", …
    timestamp: str        # ISO format
    tool_name: str
    tool_args_summary: str  # short display string (first 80 chars of str(tool_args))
    git_sha: str


class CheckpointManager:
    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd
        self._stack: list[Checkpoint] = []
        self._counter = 0

    def create(self, tool_name: str, tool_args: dict) -> Checkpoint:
        """Commit the current working-tree state and push a Checkpoint onto the stack."""
        subprocess.run(["git", "add", "-A"], cwd=self._cwd, capture_output=True)
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", f"llm-code checkpoint: before {tool_name}"],
            cwd=self._cwd,
            capture_output=True,
        )

        sha_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=self._cwd,
        )
        git_sha = sha_result.stdout.strip()

        self._counter += 1
        cp = Checkpoint(
            id=f"cp-{self._counter:03d}",
            timestamp=datetime.now(tz=timezone.utc).isoformat(),
            tool_name=tool_name,
            tool_args_summary=str(tool_args)[:80],
            git_sha=git_sha,
        )
        self._stack.append(cp)
        return cp

    def undo(self) -> Checkpoint | None:
        """Pop the last checkpoint and hard-reset the repo to that SHA."""
        if not self._stack:
            return None
        cp = self._stack.pop()
        subprocess.run(
            ["git", "reset", "--hard", cp.git_sha],
            cwd=self._cwd,
            capture_output=True,
        )
        return cp

    def list_checkpoints(self) -> list[Checkpoint]:
        return list(self._stack)

    def can_undo(self) -> bool:
        return len(self._stack) > 0
