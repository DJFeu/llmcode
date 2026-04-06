"""Validate memory content — reject derivable content that doesn't belong in memory.

Derivable content (git history, code patterns, file paths, dependency lists)
should be read from the source, not stored in memory.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_code.runtime.memory_taxonomy import MemoryType

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
