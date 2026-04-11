"""Tmux-based backend for spawning swarm members in panes."""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys


def is_tmux_available() -> bool:
    """Check if tmux is available and we are inside a tmux session."""
    return shutil.which("tmux") is not None and "TMUX" in os.environ


class TmuxBackend:
    """Spawn swarm members as tmux split panes.

    Each member runs llm-code --lite inside a new tmux pane with a role prompt.
    """

    def __init__(self) -> None:
        self._panes: dict[str, str] = {}  # member_id -> pane_id (e.g. "%5")

    def spawn(
        self,
        member_id: str,
        role: str,
        task: str,
        extra_args: tuple[str, ...] = (),
        model: str = "",
    ) -> str | None:
        """Spawn a new tmux pane running llm-code --lite.

        Returns the tmux pane ID (e.g. '%5'), or None on failure.
        """
        llm_code_bin = shutil.which("llm-code") or sys.executable
        if llm_code_bin == sys.executable:
            cmd = f"{sys.executable} -m llm_code.cli.main --lite"
        else:
            cmd = f"{llm_code_bin} --lite"

        if extra_args:
            cmd += " " + " ".join(extra_args)

        if model:
            cmd += " --model " + shlex.quote(model)

        prompt = f"You are a swarm worker with role '{role}'. Your task: {task}"
        full_cmd = f"echo {repr(prompt)} | {cmd}"

        result = subprocess.run(
            [
                "tmux", "split-window", "-h",
                "-P", "-F", "#{pane_id}",
                full_cmd,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode != 0:
            return None

        pane_id = result.stdout.strip()
        self._panes[member_id] = pane_id
        return pane_id

    def stop(self, member_id: str) -> None:
        """Kill the tmux pane for a member."""
        pane_id = self._panes.pop(member_id, None)
        if pane_id is None:
            return
        try:
            subprocess.run(
                ["tmux", "kill-pane", "-t", pane_id],
                capture_output=True,
                timeout=5,
            )
        except (subprocess.TimeoutExpired, OSError):
            pass

    def stop_all(self) -> None:
        """Kill all managed panes."""
        ids = list(self._panes.keys())
        for member_id in ids:
            self.stop(member_id)

    def is_running(self, member_id: str) -> bool:
        """Check if the pane still exists."""
        pane_id = self._panes.get(member_id)
        if pane_id is None:
            return False
        try:
            result = subprocess.run(
                ["tmux", "has-session", "-t", pane_id],
                capture_output=True,
                timeout=3,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            return False
