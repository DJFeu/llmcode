"""Permission policy for tool execution authorization."""
from __future__ import annotations

import fnmatch
from enum import Enum

from llm_code.tools.base import PermissionLevel


class PermissionMode(Enum):
    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    FULL_ACCESS = "full_access"
    PROMPT = "prompt"
    AUTO_ACCEPT = "auto_accept"


class PermissionOutcome(Enum):
    ALLOW = "allow"
    DENY = "deny"
    NEED_PROMPT = "need_prompt"


# Numeric levels for comparison (higher = more permissive)
_LEVEL_RANK: dict[PermissionLevel, int] = {
    PermissionLevel.READ_ONLY: 0,
    PermissionLevel.WORKSPACE_WRITE: 1,
    PermissionLevel.FULL_ACCESS: 2,
}

# Maximum permission level each mode allows without prompting
_MODE_MAX_LEVEL: dict[PermissionMode, int] = {
    PermissionMode.READ_ONLY: 0,
    PermissionMode.WORKSPACE_WRITE: 1,
    PermissionMode.FULL_ACCESS: 2,
    PermissionMode.AUTO_ACCEPT: 2,
    PermissionMode.PROMPT: -1,  # PROMPT handled separately
}


class PermissionPolicy:
    def __init__(
        self,
        mode: PermissionMode,
        allow_tools: frozenset[str] = frozenset(),
        deny_tools: frozenset[str] = frozenset(),
        deny_patterns: tuple[str, ...] = (),
    ) -> None:
        self._mode = mode
        self._allow_tools = allow_tools
        self._deny_tools = deny_tools
        self._deny_patterns = deny_patterns

    def authorize(
        self,
        tool_name: str,
        required: PermissionLevel,
        effective_level: PermissionLevel | None = None,
    ) -> PermissionOutcome:
        """Determine whether a tool invocation is authorized.

        Precedence:
          1. deny_tools / deny_patterns → DENY
          2. allow_tools → ALLOW
          3. AUTO_ACCEPT → always ALLOW
          4. PROMPT mode: READ_ONLY always allowed, elevated → NEED_PROMPT
          5. Other modes: compare effective level vs mode max level

        Args:
            tool_name: The name of the tool being authorized.
            required: The tool's declared required permission level.
            effective_level: If provided, used instead of ``required`` for
                level comparisons (e.g. after safety analysis determines the
                actual operation is less or more privileged than declared).
                Deny/allow lists still take full precedence.
        """
        # Use effective_level for comparisons when provided, else fall back to required
        level = effective_level if effective_level is not None else required

        # 1. Deny list and patterns always win
        if tool_name in self._deny_tools:
            return PermissionOutcome.DENY
        for pattern in self._deny_patterns:
            if fnmatch.fnmatch(tool_name, pattern):
                return PermissionOutcome.DENY

        # 2. Explicit allow list overrides mode restrictions
        if tool_name in self._allow_tools:
            return PermissionOutcome.ALLOW

        # 3. AUTO_ACCEPT allows everything
        if self._mode == PermissionMode.AUTO_ACCEPT:
            return PermissionOutcome.ALLOW

        # 4. PROMPT mode: read-only is always allowed, elevated needs prompt
        if self._mode == PermissionMode.PROMPT:
            if level == PermissionLevel.READ_ONLY:
                return PermissionOutcome.ALLOW
            return PermissionOutcome.NEED_PROMPT

        # 5. Level-based comparison for READ_ONLY, WORKSPACE_WRITE, FULL_ACCESS modes
        level_rank = _LEVEL_RANK[level]
        mode_max = _MODE_MAX_LEVEL[self._mode]
        if level_rank <= mode_max:
            return PermissionOutcome.ALLOW
        return PermissionOutcome.DENY
