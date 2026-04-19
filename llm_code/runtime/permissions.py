"""Permission policy for tool execution authorization."""
from __future__ import annotations

import fnmatch
import logging
from enum import Enum

from llm_code.tools.base import PermissionLevel

_log = logging.getLogger(__name__)


# Tools that never mutate state — allowed in PLAN mode regardless of declared level.
PLAN_MODE_READ_ONLY_TOOLS: frozenset[str] = frozenset({
    "read_file",
    "glob_search",
    "grep_search",
    "web_search",
    "web_fetch",
    "notebook_read",
    "task_get",
    "task_list",
    "swarm_list",
    "cron_list",
    "tool_search",
    "ide_diagnostics",
    "ide_selection",
    "git_status",
    "git_diff",
    "git_log",
})


def is_read_only_tool(tool_name: str) -> bool:
    """Return True if the tool is known to be safe in plan mode.

    Matches by exact name or common read-only prefixes (lsp_*, git_status/diff/log).
    """
    if tool_name in PLAN_MODE_READ_ONLY_TOOLS:
        return True
    if tool_name.startswith("lsp_"):
        return True
    return False


PLAN_MODE_DENY_MESSAGE = (
    "Plan mode active — switch to build mode (Shift+Tab) to execute mutating tools"
)


class PermissionMode(Enum):
    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    FULL_ACCESS = "full_access"
    PROMPT = "prompt"
    AUTO_ACCEPT = "auto_accept"
    PLAN = "plan"


class PermissionOutcome(Enum):
    ALLOW = "allow"
    DENY = "deny"
    NEED_PROMPT = "need_prompt"
    NEED_PLAN = "need_plan"


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
    PermissionMode.PLAN: 2,  # PLAN handled separately; max level unused but set for safety
}


def detect_shadowed_rules(
    allow_tools: frozenset[str],
    deny_tools: frozenset[str],
    mode: PermissionMode,
) -> list[str]:
    """Return warning messages for conflicting or redundant permission rules.

    Detects three categories of problems:
    - Allow rules shadowed by deny rules (same tool in both lists).
    - Allow rules that are unnecessary because the mode already allows them.
    - Deny rules that are unnecessary because the mode already blocks them.

    Args:
        allow_tools: Explicit allow list.
        deny_tools: Explicit deny list.
        mode: The active permission mode.

    Returns:
        A list of human-readable warning strings (empty when no issues found).
    """
    warnings: list[str] = []

    # 1. Allow rules shadowed by deny rules
    shadowed = allow_tools & deny_tools
    for tool in sorted(shadowed):
        warnings.append(
            f"Rule conflict: '{tool}' appears in both allow_tools and deny_tools; "
            "deny takes precedence — allow rule is ineffective."
        )

    # 2. Allow rules unnecessary because mode already allows them
    # AUTO_ACCEPT and FULL_ACCESS allow everything; WORKSPACE_WRITE allows up to
    # workspace_write level — but without per-tool level info we can only flag
    # modes that unconditionally allow all non-denied tools.
    unconditional_allow_modes = {PermissionMode.AUTO_ACCEPT, PermissionMode.FULL_ACCESS}
    if mode in unconditional_allow_modes:
        for tool in sorted(allow_tools - deny_tools):
            warnings.append(
                f"Redundant allow rule: '{tool}' is already allowed by mode '{mode.value}'; "
                "explicit allow entry has no effect."
            )

    # 3. Deny rules unnecessary because mode already blocks them
    # READ_ONLY blocks WORKSPACE_WRITE and FULL_ACCESS tools; without per-tool
    # level info we flag the case where mode=READ_ONLY and a tool is in deny_tools
    # while also not in allow_tools (i.e. it would be denied by the mode anyway).
    # The most deterministic check: PROMPT mode never auto-allows elevated tools,
    # but it does prompt — so denying explicitly is meaningful there.
    # READ_ONLY mode blocks everything above READ_ONLY already.
    if mode == PermissionMode.READ_ONLY:
        # In READ_ONLY mode all non-read-only tools are blocked anyway.
        # Explicit deny entries for tools that mode would block are redundant.
        # We flag tools that are denied but not in allow_tools (since allow overrides
        # mode, an allow+deny combo is already caught above).
        redundant_denies = deny_tools - allow_tools
        for tool in sorted(redundant_denies):
            warnings.append(
                f"Redundant deny rule: '{tool}' is already blocked by mode 'read_only'; "
                "explicit deny entry has no effect."
            )

    return warnings


class PermissionPolicy:
    def __init__(
        self,
        mode: PermissionMode,
        allow_tools: frozenset[str] = frozenset(),
        deny_tools: frozenset[str] = frozenset(),
        deny_patterns: tuple[str, ...] = (),
        rbac: object | None = None,  # RBACEngine, loosely typed to avoid circular import
    ) -> None:
        self._mode = mode
        self._allow_tools = allow_tools
        self._deny_tools = deny_tools
        self._deny_patterns = deny_patterns
        self._rbac = rbac

        # Warn about conflicting or redundant rules at construction time
        for warning in detect_shadowed_rules(allow_tools, deny_tools, mode):
            _log.warning("PermissionPolicy: %s", warning)

    @property
    def mode(self) -> "PermissionMode":
        """Return the active :class:`PermissionMode`.

        Exposed so callers (e.g. :class:`SystemPromptBuilder`) can
        branch on plan-mode without reaching into the private
        ``_mode`` attribute.
        """
        return self._mode

    def authorize(
        self,
        tool_name: str,
        required: PermissionLevel,
        effective_level: PermissionLevel | None = None,
        identity: object | None = None,  # AuthIdentity
    ) -> PermissionOutcome:
        """Determine whether a tool invocation is authorized.

        Precedence:
          0. RBAC check (if engine and identity provided) → DENY
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
            identity: Optional AuthIdentity for RBAC checks.
        """
        # Use effective_level for comparisons when provided, else fall back to required
        level = effective_level if effective_level is not None else required

        # 0. RBAC check (if engine and identity provided)
        if self._rbac is not None and identity is not None:
            if not self._rbac.is_allowed(identity, f"tool:{tool_name}"):
                return PermissionOutcome.DENY

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

        # 4b. PLAN mode: read-only tools allowed by name or declared level.
        # All mutating tools are gated (NEED_PLAN) — runtime blocks execution and
        # surfaces PLAN_MODE_DENY_MESSAGE. User switches mode (Shift+Tab) to execute.
        if self._mode == PermissionMode.PLAN:
            if is_read_only_tool(tool_name) or level == PermissionLevel.READ_ONLY:
                return PermissionOutcome.ALLOW
            return PermissionOutcome.NEED_PLAN

        # 5. Level-based comparison for READ_ONLY, WORKSPACE_WRITE, FULL_ACCESS modes
        level_rank = _LEVEL_RANK[level]
        mode_max = _MODE_MAX_LEVEL[self._mode]
        if level_rank <= mode_max:
            return PermissionOutcome.ALLOW
        return PermissionOutcome.DENY

    def allow_tool(self, tool_name: str) -> None:
        """Dynamically add a tool to the allow list (e.g. after user approves 'always')."""
        self._allow_tools = self._allow_tools | frozenset({tool_name})
