"""Project context discovery for the runtime layer."""
from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path

# Instruction filenames in priority order — first match wins per directory.
# Compatible with: opencode (AGENTS.md), Claude Code (CLAUDE.md), Cursor (.cursorrules)
INSTRUCTION_FILENAMES = (
    "AGENTS.md",
    "CLAUDE.md",
    "CONTEXT.md",  # legacy / opencode-deprecated
)


def find_instruction_files(cwd: Path) -> list[Path]:
    """Walk upward from cwd to git root, collecting all instruction files.

    Returns paths in walk order (deepest first). Project root file takes precedence.
    Also includes ~/.llmcode/INSTRUCTIONS.md as global fallback if present.
    """
    found: list[Path] = []

    # 1. Project-level: walk up from cwd to git root (or stop at filesystem root)
    current = cwd.resolve()
    git_root = current
    # Find git root if any
    for ancestor in [current, *current.parents]:
        if (ancestor / ".git").exists():
            git_root = ancestor
            break

    walked: set[Path] = set()
    while True:
        if current in walked:
            break
        walked.add(current)
        # First match per directory wins
        for name in INSTRUCTION_FILENAMES:
            candidate = current / name
            if candidate.is_file():
                found.append(candidate)
                break
        if current == git_root or current == current.parent:
            break
        current = current.parent

    # 2. Legacy .llmcode/INSTRUCTIONS.md (kept for backward compat)
    legacy = cwd / ".llmcode" / "INSTRUCTIONS.md"
    if legacy.is_file() and legacy not in found:
        found.append(legacy)

    # 3. Global ~/.llmcode/AGENTS.md
    home = Path.home() / ".llmcode" / "AGENTS.md"
    if home.is_file():
        found.append(home)

    return found


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

        # Aggregate instructions from all discovered files (multi-file fallback)
        instruction_parts: list[str] = []
        for path in find_instruction_files(cwd):
            try:
                content = path.read_text(encoding="utf-8")
                if content.strip():
                    instruction_parts.append(f"# Instructions from: {path}\n\n{content}")
            except OSError:
                continue
        instructions = "\n\n---\n\n".join(instruction_parts)

        return cls(
            cwd=cwd,
            is_git_repo=is_git,
            git_status=git_status,
            instructions=instructions,
        )
