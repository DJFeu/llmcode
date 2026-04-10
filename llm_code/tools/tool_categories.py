"""Tool category sets for multi-stage agent tool filtering.

Mirrors the tiered permission model from claude-code's ``constants/tools.ts``:

    Stage 1: MCP tools → always allowed (prefix check in registry)
    Stage 2: Plan-mode bypass → ExitPlanMode always allowed in plan mode
    Stage 3: ALL_AGENT_DISALLOWED → global deny-list for every sub-agent
    Stage 4: CUSTOM_AGENT_DISALLOWED → extra denials for user-defined agents
    Stage 5: ASYNC_AGENT_ALLOWED → positive allow-list for background agents
    Stage 6: TEAMMATE_ALLOWED → swarm-only extras (future)

Design principle: stages are *additive filters* applied in order.  Each
stage can only *remove* a tool — no stage re-introduces a tool that an
earlier stage removed.  MCP tools (stage 1) bypass all subsequent stages
so external integrations always work.

Risk mitigation:
    - All sets are ``frozenset`` — immutable at module level.
    - ``filter_tools_for_agent()`` is a pure function; no global state.
    - Unknown tool names in sets are harmless (silently ignored).
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Stage 3 — Global deny-list for ALL sub-agents
# ---------------------------------------------------------------------------
# Tools that break sub-agent sandboxing, cause UX confusion, or create
# infinite recursion when invoked from a child runtime.
#
# NOTE: "agent" is intentionally NOT here.  Built-in sync agents keep the
# agent tool for two reasons:
#   1. Recursion is already guarded by AgentTool._current_depth.
#   2. Fork children must have identical tool pools for prompt cache parity
#      (adding/removing tools changes the tool_definition array sent to
#      the API, busting the byte-identical prefix).
# For async and custom agents, "agent" is blocked via CUSTOM_AGENT_DISALLOWED
# and the ASYNC_AGENT_ALLOWED positive filter (which omits "agent").
ALL_AGENT_DISALLOWED: frozenset[str] = frozenset({
    "ask_user",          # sub-agents must not interrupt the parent
    "enter_plan_mode",   # plan mode is a main-thread abstraction
    "exit_plan_mode",    # ditto
    "task_stop",         # requires access to main-thread task state
    "coordinator",       # coordinator is a top-level mode, not sub-task
})

# ---------------------------------------------------------------------------
# Stage 4 — Extra deny-list for custom (user-defined) agents
# ---------------------------------------------------------------------------
# User-authored agent definitions (loaded from markdown frontmatter) get
# tighter restrictions than built-in roles.  Start identical to Stage 3;
# extend as custom agents gain new attack surface.
CUSTOM_AGENT_DISALLOWED: frozenset[str] = frozenset({
    *ALL_AGENT_DISALLOWED,
    "agent",             # user-defined agents must not spawn sub-agents
    "swarm_create",      # only built-in orchestrators may spawn swarms
    "swarm_delete",
})

# ---------------------------------------------------------------------------
# Stage 5 — Positive allow-list for background (async) agents
# ---------------------------------------------------------------------------
# When an agent runs in the background (e.g. fork child, async delegation),
# only these tools are permitted.  Interactive tools (ask_user, plan_mode)
# are excluded by Stage 3 already; Stage 5 further restricts to file I/O,
# search, and shell — nothing that requires a live terminal prompt.
ASYNC_AGENT_ALLOWED: frozenset[str] = frozenset({
    # File I/O
    "read_file",
    "write_file",
    "edit_file",
    "multi_edit",
    "notebook_edit",
    "notebook_read",
    # Search
    "glob_search",
    "grep_search",
    # Shell
    "bash",
    # Web
    "web_search",
    "web_fetch",
    # Git (read-only safe subset)
    "git_status",
    "git_diff",
    "git_log",
    # Task management (read-only for reporting)
    "todo_write",
    # Skills
    "skill_load",
    # Tool search (deferred tool loading)
    "tool_search",
})

# ---------------------------------------------------------------------------
# Stage 6 — Swarm teammate extras (future)
# ---------------------------------------------------------------------------
# In-process teammates (swarm members) get task coordination tools on top
# of the async allow-list.  Not yet wired — placeholder for Phase 6.
TEAMMATE_EXTRA_ALLOWED: frozenset[str] = frozenset({
    "swarm_message",
    "task_plan",
    "task_verify",
    "task_close",
})

# ---------------------------------------------------------------------------
# Coordinator Mode — pure orchestration, no direct execution
# ---------------------------------------------------------------------------
# The coordinator manages a swarm of agents without executing tools itself.
# Only 4 tools: delegate (agent), cancel (task_stop), communicate
# (swarm_message), and emit structured output.
COORDINATOR_ALLOWED: frozenset[str] = frozenset({
    "agent",
    "task_stop",
    "swarm_message",
})

# ---------------------------------------------------------------------------
# MCP prefix — used by filter_tools_for_agent() to bypass all stages
# ---------------------------------------------------------------------------
MCP_TOOL_PREFIX: str = "mcp__"


def filter_tools_for_agent(
    tool_names: frozenset[str] | set[str],
    *,
    is_builtin: bool = True,
    is_async: bool = False,
    is_teammate: bool = False,
) -> frozenset[str]:
    """Apply multi-stage filtering and return the surviving tool names.

    This is a **pure function** — no side effects, no registry mutation.
    The caller uses the returned set to drive ``ToolRegistry.filtered()``.

    Parameters
    ----------
    tool_names:
        The full set of tool names available in the parent registry.
    is_builtin:
        True for built-in roles (build/plan/explore/verify/general).
        False for user-defined (markdown frontmatter) agents.
    is_async:
        True when the agent runs in the background (fork child, async task).
    is_teammate:
        True for in-process swarm teammates (future, Phase 6).

    Returns
    -------
    frozenset[str]
        Tool names that survived all applicable stages.
    """
    surviving: set[str] = set()

    for name in tool_names:
        # Stage 1: MCP tools always pass
        if name.startswith(MCP_TOOL_PREFIX):
            surviving.add(name)
            continue

        # Stage 3: Global deny-list
        if name in ALL_AGENT_DISALLOWED:
            continue

        # Stage 4: Custom agent extra deny-list
        if not is_builtin and name in CUSTOM_AGENT_DISALLOWED:
            continue

        # Stage 5: Async allow-list (positive filter)
        if is_async:
            if name in ASYNC_AGENT_ALLOWED:
                surviving.add(name)
                continue
            # Stage 6: Teammate extras on top of async
            if is_teammate and name in TEAMMATE_EXTRA_ALLOWED:
                surviving.add(name)
                continue
            # Not in any async allow-list → blocked
            continue

        # Sync agent — passed all deny checks
        surviving.add(name)

    return frozenset(surviving)
