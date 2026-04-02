"""Specialized agent role definitions for constrained sub-agents."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentRole:
    name: str
    description: str
    system_prompt_prefix: str
    allowed_tools: frozenset[str]
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

BUILT_IN_ROLES: dict[str, AgentRole] = {
    "explore": EXPLORE_ROLE,
    "plan": PLAN_ROLE,
    "verify": VERIFICATION_ROLE,
}
