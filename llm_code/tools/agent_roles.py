"""Specialized agent role definitions for constrained sub-agents."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentRole:
    name: str
    description: str
    system_prompt_prefix: str
    # Sentinel convention:
    #   None           -> unrestricted (full inheritance from parent registry)
    #   frozenset()    -> deny-all (no tools allowed)
    #   frozenset({…}) -> strict whitelist
    allowed_tools: frozenset[str] | None
    model_key: str  # key in config.model_routing


EXPLORE_ROLE = AgentRole(
    name="explore",
    description="Read-only code exploration agent",
    system_prompt_prefix=(
        "You are a read-only exploration agent. You MUST NOT create, modify, or delete any files. "
        "Only use read-only tools to explore the codebase."
    ),
    allowed_tools=frozenset({
        "read_file",
        "glob_search",
        "grep_search",
        "git_status",
        "git_diff",
        "git_log",
        "lsp_goto_definition",
        "lsp_find_references",
        "lsp_diagnostics",
    }),
    model_key="sub_agent",
)

PLAN_ROLE = AgentRole(
    name="plan",
    description="Planning-only agent that analyzes and plans but never executes changes",
    system_prompt_prefix=(
        "You are a planning agent. Analyze the codebase and create a detailed plan. "
        "Do NOT execute any changes — only describe what should be done, which files to modify, "
        "and what the implementation should look like."
    ),
    allowed_tools=frozenset({
        "read_file",
        "glob_search",
        "grep_search",
        "git_status",
        "git_diff",
        "git_log",
        "memory_recall",
        "memory_list",
    }),
    model_key="sub_agent",
)

VERIFICATION_ROLE = AgentRole(
    name="verify",
    description="Adversarial verification agent that tries to find problems",
    system_prompt_prefix=(
        "You are a verification agent. Your job is to find problems.\n"
        "Run tests, linters, and type checkers. Check exit codes and output carefully.\n"
        "Do NOT trust the implementer's claims. Verify independently.\n"
        "Report VERDICT: PASS, FAIL, or PARTIAL with evidence for each check."
    ),
    allowed_tools=frozenset({
        "read_file",
        "glob_search",
        "grep_search",
        "bash",
        "git_status",
        "git_diff",
    }),
    model_key="sub_agent",
)

BUILD_ROLE = AgentRole(
    name="build",
    description="Default agent — full tool access for writing and shipping code",
    system_prompt_prefix=(
        "You are the build agent — the primary code-writing role. You may "
        "read, search, write, edit, and execute. Use the most direct path "
        "to a working solution; spawn subagents only when a task is clearly "
        "independent and can be parallelized."
    ),
    allowed_tools=None,
    model_key="primary",
)

GENERAL_ROLE = AgentRole(
    name="general",
    description="Multi-purpose subagent for focused single-task delegation",
    system_prompt_prefix=(
        "You are a general-purpose subagent. Complete the single task you "
        "were dispatched for and report back. Do not manage your own task "
        "list — the parent agent owns coordination."
    ),
    allowed_tools=frozenset({
        "read_file",
        "write_file",
        "edit_file",
        "multi_edit",
        "glob_search",
        "grep_search",
        "bash",
        "git_status",
        "git_diff",
        "git_log",
        "lsp_goto_definition",
        "lsp_find_references",
        "lsp_diagnostics",
        "lsp_hover",
        "lsp_document_symbol",
        "lsp_workspace_symbol",
        "web_fetch",
        "web_search",
    }),
    model_key="sub_agent",
)

BUILT_IN_ROLES: dict[str, AgentRole] = {
    "build": BUILD_ROLE,
    "plan": PLAN_ROLE,
    "explore": EXPLORE_ROLE,
    "verify": VERIFICATION_ROLE,
    "general": GENERAL_ROLE,
}


def is_tool_allowed_for_role(role: AgentRole | None, tool_name: str) -> bool:
    """Return True if tool_name is callable under role.

    Sentinel rules:
      * role is None              -> unrestricted (True)
      * role.allowed_tools is None -> unrestricted (True)
      * role.allowed_tools == frozenset() -> deny-all (False)
      * otherwise -> strict whitelist membership
    """
    if role is None:
        return True
    if role.allowed_tools is None:
        return True
    return tool_name in role.allowed_tools
