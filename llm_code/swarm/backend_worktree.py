"""Git worktree backend for spawning swarm members in isolated filesystem copies."""
from __future__ import annotations

import asyncio
import dataclasses
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from llm_code.runtime.config import WorktreeConfig


@dataclasses.dataclass(frozen=True)
class WorktreeResult:
    """Result from completing a worktree-backed swarm member."""

    member_id: str
    status: str  # "success" | "conflict" | "empty" | "error"
    diff: str = ""
    branch_name: str = ""
    conflict_files: tuple[str, ...] = ()
    message: str = ""


class WorktreeBackend:
    """Spawn swarm members in git worktrees for isolated file system access.

    Lifecycle per member:
      spawn    -> create worktree on a new branch, copy gitignored files, start llm-code --lite
      stop     -> terminate the subprocess
      complete -> commit changes, apply on_complete strategy, optionally cleanup
    """

    def __init__(self, project_dir: Path, config: WorktreeConfig) -> None:
        self._project_dir = Path(project_dir)
        self._config = config
        # member_id -> worktree path
        self._worktrees: dict[str, Path] = {}
        # member_id -> asyncio/subprocess process
        self._procs: dict[str, Any] = {}
        # member_id -> branch name
        self._branch_names: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def spawn(
        self,
        member_id: str,
        role: str,
        task: str,
        model: str = "",
        extra_args: tuple[str, ...] = (),
    ) -> int | None:
        """Create a git worktree for member_id and launch llm-code --lite.

        Returns the PID of the launched process, or None on failure.
        """
        base = Path(self._config.base_dir) if self._config.base_dir else Path("/tmp")
        wt_path = base / f"llm-code-wt-{member_id}"
        branch_name = f"agent/{member_id}"

        # Create the worktree on a new branch
        subprocess.run(
            ["git", "worktree", "add", str(wt_path), "-b", branch_name],
            cwd=str(self._project_dir),
            capture_output=True,
            text=True,
        )

        # Copy gitignored files into the worktree
        for rel_path in self._config.copy_gitignored:
            src = self._project_dir / rel_path
            dst = wt_path / rel_path
            if src.exists():
                try:
                    shutil.copy2(str(src), str(dst))
                except OSError:
                    pass

        self._worktrees[member_id] = wt_path
        self._branch_names[member_id] = branch_name

        # Build the llm-code --lite command
        llm_code_bin = shutil.which("llm-code") or sys.executable
        cmd_args: list[str] = []
        if llm_code_bin == sys.executable:
            cmd_args = [sys.executable, "-m", "llm_code.cli.main", "--lite"]
        else:
            cmd_args = [llm_code_bin, "--lite"]

        cmd_args = list(cmd_args) + list(extra_args)
        if model:
            cmd_args.extend(["--model", model])

        prompt = f"You are a swarm worker with role '{role}'. Your task: {task}"

        proc = await asyncio.create_subprocess_exec(
            *cmd_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(wt_path),
        )
        self._procs[member_id] = proc

        if proc.stdin:
            proc.stdin.write((prompt + "\n").encode())
            await proc.stdin.drain()

        return proc.pid

    async def stop(self, member_id: str) -> None:
        """Terminate the process for a member (without completing/merging)."""
        proc = self._procs.get(member_id)
        if proc is None:
            return
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except (ProcessLookupError, asyncio.TimeoutError):
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        self._procs.pop(member_id, None)

    async def complete(self, member_id: str) -> WorktreeResult:
        """Finalise a member's work according to config.on_complete.

        Strategies:
          "diff"   - commit all changes, capture diff, cleanup worktree+branch
          "merge"  - commit, merge into current branch, handle conflicts
          "branch" - commit, remove worktree, keep branch (for later review)

        Unknown member_id returns an error result immediately.
        """
        if member_id not in self._worktrees:
            return WorktreeResult(
                member_id=member_id,
                status="error",
                message=f"No worktree registered for member '{member_id}'",
            )

        wt_path = self._worktrees[member_id]
        branch_name = self._branch_names[member_id]
        on_complete = self._config.on_complete

        try:
            if on_complete == "diff":
                return await self._complete_diff(member_id, wt_path, branch_name)
            elif on_complete == "merge":
                return await self._complete_merge(member_id, wt_path, branch_name)
            elif on_complete == "branch":
                return await self._complete_branch(member_id, wt_path, branch_name)
            else:
                return WorktreeResult(
                    member_id=member_id,
                    status="error",
                    message=f"Unknown on_complete strategy: '{on_complete}'",
                )
        except Exception as exc:
            return WorktreeResult(
                member_id=member_id,
                status="error",
                message=str(exc),
            )

    async def stop_all(self) -> None:
        """Stop all running member processes."""
        ids = list(self._procs.keys())
        for member_id in ids:
            await self.stop(member_id)

    def is_running(self, member_id: str) -> bool:
        """Return True if the member process is still alive."""
        proc = self._procs.get(member_id)
        if proc is None:
            return False
        return getattr(proc, "returncode", None) is None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _commit_worktree(self, wt_path: Path) -> None:
        """Stage all changes and create a commit in the worktree."""
        wt = str(wt_path)
        subprocess.run(["git", "add", "-A"], cwd=wt, capture_output=True, text=True)
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "swarm: agent work complete"],
            cwd=wt,
            capture_output=True,
            text=True,
        )

    def _remove_worktree(self, wt_path: Path, branch_name: str, remove_branch: bool = True) -> None:
        """Remove the worktree and optionally delete the branch."""
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(wt_path)],
            cwd=str(self._project_dir),
            capture_output=True,
            text=True,
        )
        if remove_branch:
            subprocess.run(
                ["git", "branch", "-d", branch_name],
                cwd=str(self._project_dir),
                capture_output=True,
                text=True,
            )

    async def _complete_diff(
        self, member_id: str, wt_path: Path, branch_name: str
    ) -> WorktreeResult:
        """Commit, capture diff, then cleanup worktree and branch."""
        self._commit_worktree(wt_path)

        diff_result = subprocess.run(
            ["git", "diff", "HEAD~1..HEAD"],
            cwd=str(wt_path),
            capture_output=True,
            text=True,
        )
        diff_text = diff_result.stdout

        if self._config.cleanup_on_success:
            self._remove_worktree(wt_path, branch_name, remove_branch=True)
            self._worktrees.pop(member_id, None)
            self._branch_names.pop(member_id, None)
            self._procs.pop(member_id, None)

        return WorktreeResult(
            member_id=member_id,
            status="success",
            diff=diff_text,
            branch_name=branch_name,
        )

    async def _complete_merge(
        self, member_id: str, wt_path: Path, branch_name: str
    ) -> WorktreeResult:
        """Commit agent work, merge into current HEAD branch, handle conflicts."""
        self._commit_worktree(wt_path)

        merge_result = subprocess.run(
            ["git", "merge", "--no-ff", branch_name, "-m", f"Merge {branch_name}"],
            cwd=str(self._project_dir),
            capture_output=True,
            text=True,
        )

        if merge_result.returncode != 0:
            status_result = subprocess.run(
                ["git", "diff", "--name-only", "--diff-filter=U"],
                cwd=str(self._project_dir),
                capture_output=True,
                text=True,
            )
            conflict_files = tuple(
                f for f in status_result.stdout.splitlines() if f.strip()
            )
            return WorktreeResult(
                member_id=member_id,
                status="conflict",
                branch_name=branch_name,
                conflict_files=conflict_files,
                message=merge_result.stderr,
            )

        if self._config.cleanup_on_success:
            self._remove_worktree(wt_path, branch_name, remove_branch=True)
            self._worktrees.pop(member_id, None)
            self._branch_names.pop(member_id, None)
            self._procs.pop(member_id, None)

        return WorktreeResult(
            member_id=member_id,
            status="success",
            branch_name=branch_name,
        )

    async def _complete_branch(
        self, member_id: str, wt_path: Path, branch_name: str
    ) -> WorktreeResult:
        """Commit agent work, remove worktree, keep branch for later review."""
        self._commit_worktree(wt_path)

        subprocess.run(
            ["git", "worktree", "remove", "--force", str(wt_path)],
            cwd=str(self._project_dir),
            capture_output=True,
            text=True,
        )
        self._worktrees.pop(member_id, None)
        self._branch_names.pop(member_id, None)
        self._procs.pop(member_id, None)

        return WorktreeResult(
            member_id=member_id,
            status="success",
            branch_name=branch_name,
        )
