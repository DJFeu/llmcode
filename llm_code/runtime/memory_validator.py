"""Validate memory content — reject derivable content that doesn't belong in memory.

Derivable content (git history, code patterns, file paths, dependency lists)
should be read from the source, not stored in memory.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_code.runtime.memory_taxonomy import MemoryType

logger = logging.getLogger(__name__)


class DerivableContentError(ValueError):
    """Raised in strict mode when memory text contains derivable artifacts."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("Derivable content rejected: " + "; ".join(reasons))
        self.reasons = tuple(reasons)


_FENCED_CODE_RE = re.compile(r"```")
_GIT_SHA_RE = re.compile(r"\b[0-9a-f]{40}\b")
_ABS_PATH_RE = re.compile(r"(?<![\w/])(/[\w./\-]+)")


def _find_derivable(text: str, repo_root: Path) -> list[str]:
    reasons: list[str] = []
    if _FENCED_CODE_RE.search(text):
        reasons.append("contains fenced code blocks (```)")
    if _GIT_SHA_RE.search(text):
        reasons.append("contains 40-char git SHA")
    try:
        root_resolved = repo_root.resolve()
    except OSError:
        root_resolved = repo_root
    for match in _ABS_PATH_RE.findall(text):
        candidate = Path(match)
        try:
            if not candidate.exists():
                continue
            resolved = candidate.resolve()
        except OSError:
            continue
        try:
            resolved.relative_to(root_resolved)
        except ValueError:
            continue
        reasons.append(f"references on-disk path under repo root: {match}")
        break
    return reasons


def validate_non_derivable(
    text: str,
    repo_root: Path,
    *,
    strict: bool = False,
) -> None:
    """Reject memory text that duplicates derivable artifacts.

    In strict mode, raises :class:`DerivableContentError` when *text* contains
    fenced code blocks, 40-char git SHAs, or absolute paths that exist under
    *repo_root*. In the default warn-only mode (``strict=False``), logs a
    warning and returns silently.
    """
    reasons = _find_derivable(text, repo_root)
    if not reasons:
        return
    if strict:
        raise DerivableContentError(reasons)
    logger.warning(
        "memory.derivable_content_warning: %s",
        "; ".join(reasons),
    )

# Patterns that indicate derivable content
_GIT_LOG_PATTERN = re.compile(r"^[a-f0-9]{7,40}\s+\w", re.MULTILINE)
_FILE_PATH_HEAVY = re.compile(r"(?:^|\n)\s*[-*]\s*/[\w/]+\.\w+", re.MULTILINE)
_DEPENDENCY_LIST = re.compile(
    r"(?:requirements\.txt|package\.json|Cargo\.toml|go\.mod|pyproject\.toml)",
    re.IGNORECASE,
)
_CODE_BLOCK_HEAVY = re.compile(r"```[\s\S]*?```")
_IMPORT_HEAVY = re.compile(r"^(?:import |from .+ import |const .+ = require)", re.MULTILINE)


def validate_content(content: str, memory_type: "MemoryType") -> tuple[bool, str]:
    """Validate whether content is appropriate for memory storage.

    Returns:
        (True, "") if valid, (False, reason) if rejected.
    """
    # Empty content is always invalid
    if not content.strip():
        return False, "Content is empty"

    # Size check (soft — hard limit enforced by TypedMemoryStore)
    if len(content) > 10_000:
        return False, "Content too long (>10,000 chars). Summarize before storing."

    # Check for git log output (looks like commit hashes + messages)
    git_matches = _GIT_LOG_PATTERN.findall(content)
    if len(git_matches) >= 5:
        return False, "Content looks like git log output. Use `git log` to read this — don't store it."

    # Check for heavy file path listings
    path_matches = _FILE_PATH_HEAVY.findall(content)
    content_lines = content.count("\n") + 1
    if path_matches and len(path_matches) > content_lines * 0.5 and len(path_matches) >= 5:
        return False, "Content is mostly file paths. Use `glob_search` to find files — don't store paths."

    # Check for code-heavy content (>60% is code blocks)
    code_blocks = _CODE_BLOCK_HEAVY.findall(content)
    code_chars = sum(len(b) for b in code_blocks)
    if code_chars > len(content) * 0.6 and len(content) > 200:
        return False, "Content is mostly code. Read the source file instead of storing code in memory."

    # Check for import-heavy content (dependency lists)
    import_matches = _IMPORT_HEAVY.findall(content)
    if len(import_matches) >= 8:
        return False, "Content looks like import/dependency listings. These are derivable from source."

    return True, ""
