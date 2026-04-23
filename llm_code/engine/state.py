"""Shared `State` dict and `MemoryScope` enum for the v12 engine.

`State` is the single source of truth passed between Components in a
Pipeline (M2). Components declare the keys they read/write via the
`@component` decorator; at Pipeline build time, conflicting writes are
detected and raise.

`MemoryScope` lives here (not under `engine/components/memory/` which is
built in M7) because `AgentLoopConfig` and `HayhooksConfig` reference it
at config-load time — placing it in the scaffolding module avoids an
import cycle.

This module is frozen for M0: additions land per-milestone alongside the
Components that read them. Keys below are the canonical core used across
M1–M8; Components may extend the dict with their own keys provided they
declare `state_writes` at registration time (M2).
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal, TypedDict

Mode = Literal["build", "plan", "explore", "verify", "general"]


class MemoryScope(str, Enum):
    """Scope of a memory entry.

    - ``SESSION``: entry lives only for the current agent session.
    - ``PROJECT``: entry is scoped to the current project directory.
    - ``GLOBAL``: entry is available across all projects.
    """

    SESSION = "session"
    PROJECT = "project"
    GLOBAL = "global"


class State(TypedDict, total=False):
    """Engine State dict passed through the Pipeline.

    All keys are optional (``total=False``) so a Component receives only
    what it needs. The canonical keys are documented here; Components may
    add their own keys via state-write declarations (M2).
    """

    messages: list[Any]
    tool_calls: list[Any]
    tool_results: list[Any]
    iteration: int
    last_error: BaseException | None
    degraded: bool
    mode: Mode
    denial_history: list[Any]
    memory_entries: list[Any]
    allowed_tools: frozenset[str]
