"""Structured git diff parsing + auto-commit message (M5).

``parse_unified_diff`` reshapes ``git diff`` text into a list of
:class:`FileDiff` entries (path + additions + deletions) so the TUI
can render coloured diff summaries and the marketplace auto-commit
hook has something to inspect.

``build_auto_commit_message`` distils the last assistant message /
file list into a short conventional-commit line.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class FileDiff:
    path: str
    additions: int
    deletions: int


_FILE_HEADER_RE = re.compile(r"^diff --git a/(.+?) b/\1", re.MULTILINE)


def parse_unified_diff(text: str) -> tuple[FileDiff, ...]:
    if not text.strip():
        return ()
    # Split the diff on ``diff --git`` headers. First slice is empty
    # (text before the first header), skip it.
    chunks = re.split(r"(?m)^diff --git ", text)
    files: list[FileDiff] = []
    for chunk in chunks[1:]:
        head_line, *rest = chunk.splitlines()
        m = re.match(r"a/(.+?) b/\1", head_line)
        if not m:
            continue
        path = m.group(1)
        additions = sum(
            1 for ln in rest
            if ln.startswith("+") and not ln.startswith("+++")
        )
        deletions = sum(
            1 for ln in rest
            if ln.startswith("-") and not ln.startswith("---")
        )
        files.append(FileDiff(path=path, additions=additions, deletions=deletions))
    return tuple(files)


def build_auto_commit_message(
    *, last_assistant_text: str, files_changed: list[str],
) -> str:
    """Compose a short conventional-style commit message."""
    text = (last_assistant_text or "").strip().splitlines()
    summary = ""
    if text:
        summary = text[0][:70]
    if not summary:
        n = len(files_changed)
        if n == 1:
            summary = f"update {files_changed[0]}"
        else:
            summary = f"update {n} file{'s' if n != 1 else ''}"
    return f"auto: {summary}"
