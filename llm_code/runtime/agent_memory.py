"""Agent memory scope management — three persistence layers.

Each agent can opt into one of three memory scopes:

    user:    ~/.llm-code/agent-memory/<agent_type>/
             Persists across all projects for this user.

    project: .llm-code/agent-memory/<agent_type>/
             Persists within the project (committed to VCS).

    local:   .llm-code/agent-memory-local/<agent_type>/
             Persists within the project but NOT in VCS.
             (.gitignore should include ``.llm-code/agent-memory-local/``)

Agents with memory enabled automatically get file read/write/edit
tools injected into their allowed_tools set (see ``inject_memory_tools``).

Risk mitigations:
    - Paths are sanitised (colons replaced with dashes for Windows compat).
    - Memory directories are created lazily, never eagerly.
    - ``resolve_memory_dir`` is a pure function; no side effects.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

MemoryScope = Literal["user", "project", "local"]

# Tools that agents need for memory operations
MEMORY_TOOLS: frozenset[str] = frozenset({
    "read_file",
    "write_file",
    "edit_file",
})


def _sanitise_name(name: str) -> str:
    """Sanitise agent type name for use as a directory name.

    Replaces colons and other unsafe characters with dashes.
    """
    return re.sub(r"[:/\\<>|\"?*]", "-", name).strip("-") or "unnamed"


def resolve_memory_dir(
    agent_type: str,
    scope: MemoryScope,
    project_path: Path | None = None,
) -> Path:
    """Resolve the memory directory path for an agent.

    Does NOT create the directory — callers must ``mkdir(parents=True,
    exist_ok=True)`` if they need it to exist.

    Parameters
    ----------
    agent_type:
        The agent's role name (e.g. "security-auditor").
    scope:
        One of "user", "project", "local".
    project_path:
        Root of the current project.  Required for "project" and "local"
        scopes; ignored for "user".

    Raises
    ------
    ValueError
        If *scope* is "project" or "local" but *project_path* is None.
    """
    safe_name = _sanitise_name(agent_type)

    if scope == "user":
        return Path.home() / ".llm-code" / "agent-memory" / safe_name

    if project_path is None:
        raise ValueError(
            f"project_path is required for memory scope '{scope}'"
        )

    if scope == "project":
        return project_path / ".llm-code" / "agent-memory" / safe_name

    if scope == "local":
        return project_path / ".llm-code" / "agent-memory-local" / safe_name

    raise ValueError(f"Unknown memory scope: {scope!r}")


def inject_memory_tools(
    allowed_tools: frozenset[str] | None,
) -> frozenset[str] | None:
    """Ensure file read/write/edit tools are in the allowed set.

    If *allowed_tools* is ``None`` (unrestricted), returns ``None``
    (still unrestricted).  Otherwise returns the union with
    ``MEMORY_TOOLS``.
    """
    if allowed_tools is None:
        return None
    return allowed_tools | MEMORY_TOOLS
