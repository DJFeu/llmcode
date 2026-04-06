"""Project context discovery for the runtime layer."""
from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path


@dataclasses.dataclass(frozen=True)
class ProjectContext:
    cwd: Path
    is_git_repo: bool
    git_status: str
    instructions: str

    @classmethod
    def discover(cls, cwd: Path) -> "ProjectContext":
        """Discover project context from the given working directory."""
        is_git = (cwd / ".git").exists()

        git_status = ""
        if is_git:
            try:
                result = subprocess.run(
                    ["git", "status", "--short"],
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    git_status = result.stdout.rstrip("\n")
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

        instructions = ""
        instructions_path = cwd / ".llmcode" / "INSTRUCTIONS.md"
        if instructions_path.exists():
            try:
                instructions = instructions_path.read_text(encoding="utf-8")
            except OSError:
                pass

        return cls(
            cwd=cwd,
            is_git_repo=is_git,
            git_status=git_status,
            instructions=instructions,
        )
