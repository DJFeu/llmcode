"""Per-directory instruction attachment for read_file results.

When the LLM reads a file, walk up from its directory and attach any
AGENTS.md / CLAUDE.md it finds along the way (until git root or
filesystem root). Each instruction file is attached at most once per
session to avoid spamming context.
"""
from __future__ import annotations

from pathlib import Path

from llm_code.runtime.context import INSTRUCTION_FILENAMES

# Per-process cache: paths already attached this session.
# Cleared by clear_attached() when starting a new session.
_attached: set[str] = set()


def clear_attached() -> None:
    """Reset the attachment cache (call at session start)."""
    _attached.clear()


def find_nearby_instructions(file_path: Path) -> list[Path]:
    """Walk upward from a file's directory to find instruction files.

    Stops at git root or filesystem root. Returns paths in walk order
    (closest to file first).
    """
    if not file_path.exists():
        return []

    found: list[Path] = []
    current = file_path.resolve()
    if current.is_file():
        current = current.parent

    # Find git root for upper bound
    git_root: Path | None = None
    for ancestor in [current, *current.parents]:
        if (ancestor / ".git").exists():
            git_root = ancestor
            break

    walked: set[Path] = set()
    while True:
        if current in walked:
            break
        walked.add(current)
        # First match per directory
        for name in INSTRUCTION_FILENAMES:
            candidate = current / name
            if candidate.is_file():
                found.append(candidate)
                break
        if git_root and current == git_root:
            break
        if current == current.parent:
            break
        current = current.parent

    return found


def attach_for(file_path: Path, base_instructions: set[str] | None = None) -> str:
    """Return a markdown footer with new instructions discovered for this file.

    Args:
        file_path: The file being read by the LLM.
        base_instructions: Set of instruction file paths already in the system
                           prompt — these are skipped (no duplication).

    Returns:
        A formatted footer string (empty if nothing new to attach).
    """
    nearby = find_nearby_instructions(file_path)
    if not nearby:
        return ""

    base = base_instructions or set()
    new_attachments: list[str] = []

    for instruction_file in nearby:
        path_str = str(instruction_file)
        if path_str in base or path_str in _attached:
            continue
        try:
            content = instruction_file.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not content:
            continue
        _attached.add(path_str)
        new_attachments.append(
            f"\n\n--- Instructions from {instruction_file} ---\n{content}"
        )

    if not new_attachments:
        return ""

    return "\n\n[Auto-attached directory instructions:]" + "".join(new_attachments)


__all__ = ["clear_attached", "find_nearby_instructions", "attach_for"]
