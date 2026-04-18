"""Session-mode discrimination (M3).

Distinguishes interactive sessions (REPL) from automation paths
(headless / SDK). Non-interactive callers can't be prompted, so the
runtime auto-approves a curated set of read-only tools to avoid
deadlocks while still refusing destructive operations without an
explicit user nod.
"""
from __future__ import annotations

from enum import Enum


class SessionMode(Enum):
    INTERACTIVE = "interactive"  # REPL — can prompt user
    HEADLESS = "headless"        # one-shot CLI — no prompts
    SDK = "sdk"                  # embedded in third-party app


def from_string(value: str) -> "SessionMode":
    for m in SessionMode:
        if m.value == value.lower():
            return m
    raise ValueError(
        f"unknown SessionMode {value!r}; "
        f"expected one of {[m.value for m in SessionMode]}"
    )


# Tools safe to auto-approve in non-interactive modes.
# Hand-curated — anything with side effects stays off this list.
_SAFE_READ_ONLY_TOOLS: frozenset[str] = frozenset({
    "read_file",
    "glob_search",
    "grep_search",
    "git_status",
    "git_diff",
    "git_log",
    "lsp_goto_definition",
    "lsp_find_references",
    "lsp_diagnostics",
    "lsp_hover",
    "lsp_document_symbol",
    "lsp_workspace_symbol",
    "memory_recall",
    "memory_list",
    "web_fetch",     # read-only by HTTP semantics (GET)
})


def auto_approve_safe(mode: SessionMode, tool_name: str) -> bool:
    """Return True when ``mode`` allows auto-approving ``tool_name``.

    Interactive mode never auto-approves (user owns the permission
    dialog). Headless / SDK auto-approve known read-only tools and
    fall back to the normal policy for everything else.
    """
    if mode is SessionMode.INTERACTIVE:
        return False
    return tool_name in _SAFE_READ_ONLY_TOOLS
