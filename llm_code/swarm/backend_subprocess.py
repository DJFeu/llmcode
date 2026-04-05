"""Subprocess-based backend for spawning swarm members."""
from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path
from typing import IO


class SubprocessBackend:
    """Spawn swarm members as asyncio subprocesses.

    Each member runs llm-code --lite with a role prompt piped to stdin.
    Output is captured to swarm/<id>/output.log.
    """

    def __init__(self, swarm_dir: Path) -> None:
        self._swarm_dir = Path(swarm_dir)
        self._swarm_dir.mkdir(parents=True, exist_ok=True)
        self._procs: dict[str, asyncio.subprocess.Process] = {}

    async def spawn(
        self,
        member_id: str,
        role: str,
        task: str,
        extra_args: tuple[str, ...] = (),
        model: str = "",
    ) -> int | None:
        """Spawn a new llm-code --lite process for this member.

        Returns the PID, or None on failure.
        """
        member_dir = self._swarm_dir / member_id
        member_dir.mkdir(parents=True, exist_ok=True)
        log_path = member_dir / "output.log"
        log_path.touch()

        llm_code_bin = shutil.which("llm-code") or sys.executable
        cmd_args: list[str] = []
        if llm_code_bin == sys.executable:
            cmd_args = [sys.executable, "-m", "llm_code.cli.tui_main", "--lite"]
        else:
            cmd_args = [llm_code_bin, "--lite"]

        cmd_args.extend(extra_args)

        if model:
            cmd_args.extend(["--model", model])

        prompt = f"You are a swarm worker with role '{role}'. Your task: {task}"

        log_fh = open(log_path, "w", encoding="utf-8")
        proc = await asyncio.create_subprocess_exec(
            *cmd_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=log_fh,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._procs[member_id] = proc
        # Track open file handles for cleanup
        if not hasattr(self, "_log_files"):
            self._log_files: dict[str, IO[str]] = {}
        self._log_files[member_id] = log_fh

        # Send initial prompt
        if proc.stdin:
            proc.stdin.write((prompt + "\n").encode())
            await proc.stdin.drain()

        return proc.pid

    async def stop(self, member_id: str) -> None:
        """Terminate the process for a member."""
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
        # Close log file handle
        if hasattr(self, "_log_files"):
            fh = self._log_files.pop(member_id, None)
            if fh is not None:
                try:
                    fh.close()
                except Exception:
                    pass

    async def stop_all(self) -> None:
        """Terminate all spawned processes."""
        ids = list(self._procs.keys())
        for member_id in ids:
            await self.stop(member_id)

    def is_running(self, member_id: str) -> bool:
        """Check if a member process is still alive."""
        proc = self._procs.get(member_id)
        if proc is None:
            return False
        return proc.returncode is None
