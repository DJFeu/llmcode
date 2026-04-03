"""Unified diff generation for file edits."""
from __future__ import annotations

import dataclasses
import difflib
import re


@dataclasses.dataclass(frozen=True)
class DiffHunk:
    """A single hunk from a unified diff."""

    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    lines: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "old_start": self.old_start,
            "old_lines": self.old_lines,
            "new_start": self.new_start,
            "new_lines": self.new_lines,
            "lines": list(self.lines),
        }


_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

MAX_DIFF_LINES = 500


def generate_diff(
    old: str,
    new: str,
    filename: str,
    context: int = 3,
) -> list[DiffHunk]:
    """Generate structured diff hunks from old and new file content.

    Uses difflib.unified_diff with the given context (default 3).
    Truncates total output lines at MAX_DIFF_LINES.
    """
    if old == new:
        return []

    diff_lines = list(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{filename}",
            tofile=f"b/{filename}",
            n=context,
        )
    )

    hunks: list[DiffHunk] = []
    current_lines: list[str] = []
    old_start = new_start = old_count = new_count = 0
    total_lines = 0

    for raw_line in diff_lines:
        # Skip the --- and +++ header lines
        if raw_line.startswith("---") or raw_line.startswith("+++"):
            continue

        m = _HUNK_HEADER.match(raw_line)
        if m:
            # Flush previous hunk
            if current_lines:
                hunks.append(DiffHunk(
                    old_start=old_start,
                    old_lines=old_count,
                    new_start=new_start,
                    new_lines=new_count,
                    lines=tuple(current_lines),
                ))
            old_start = int(m.group(1))
            old_count = int(m.group(2)) if m.group(2) else 1
            new_start = int(m.group(3))
            new_count = int(m.group(4)) if m.group(4) else 1
            current_lines = []
            continue

        if total_lines >= MAX_DIFF_LINES:
            break

        # Normalize: strip trailing newline, keep prefix (+/-/space)
        stripped = raw_line.rstrip("\n\r")
        current_lines.append(stripped)
        total_lines += 1

    # Flush final hunk
    if current_lines:
        hunks.append(DiffHunk(
            old_start=old_start,
            old_lines=old_count,
            new_start=new_start,
            new_lines=new_count,
            lines=tuple(current_lines),
        ))

    return hunks


def count_changes(hunks: list[DiffHunk]) -> tuple[int, int]:
    """Count total additions and deletions across all hunks."""
    adds = sum(1 for h in hunks for line in h.lines if line.startswith("+"))
    dels = sum(1 for h in hunks for line in h.lines if line.startswith("-"))
    return adds, dels
