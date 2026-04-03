"""Default task profiles mapping each TaskType to its tool/memory/governance set."""
from __future__ import annotations

from llm_code.hida.types import TaskProfile, TaskType

# Core tool sets shared across profiles
_FILE_READ = frozenset({"read_file", "glob_search", "grep_search"})
_FILE_WRITE = frozenset({"write_file", "edit_file"})
_SHELL = frozenset({"bash"})
_MEMORY = frozenset({"memory_store", "memory_recall", "memory_list"})
_GIT = frozenset({"git_diff", "git_log", "git_status"})
_AGENT = frozenset({"agent"})

DEFAULT_PROFILES: dict[TaskType, TaskProfile] = {
    TaskType.CODING: TaskProfile(
        task_type=TaskType.CODING,
        confidence=1.0,
        tools=_FILE_READ | _FILE_WRITE | _SHELL | _AGENT,
        memory_keys=frozenset({"project_stack", "coding_style", "architecture"}),
        governance_categories=frozenset({"coding"}),
        load_full_prompt=False,
    ),
    TaskType.DEBUGGING: TaskProfile(
        task_type=TaskType.DEBUGGING,
        confidence=1.0,
        tools=_FILE_READ | _SHELL | _AGENT,
        memory_keys=frozenset({"known_issues", "project_stack"}),
        governance_categories=frozenset({"debugging"}),
        load_full_prompt=False,
    ),
    TaskType.REVIEWING: TaskProfile(
        task_type=TaskType.REVIEWING,
        confidence=1.0,
        tools=_FILE_READ | _GIT,
        memory_keys=frozenset({"coding_style", "review_guidelines"}),
        governance_categories=frozenset({"reviewing"}),
        load_full_prompt=False,
    ),
    TaskType.PLANNING: TaskProfile(
        task_type=TaskType.PLANNING,
        confidence=1.0,
        tools=_FILE_READ | _MEMORY | _AGENT,
        memory_keys=frozenset({"architecture", "project_stack", "roadmap"}),
        governance_categories=frozenset({"planning"}),
        load_full_prompt=False,
    ),
    TaskType.TESTING: TaskProfile(
        task_type=TaskType.TESTING,
        confidence=1.0,
        tools=_FILE_READ | _FILE_WRITE | _SHELL,
        memory_keys=frozenset({"project_stack", "test_patterns"}),
        governance_categories=frozenset({"testing"}),
        load_full_prompt=False,
    ),
    TaskType.REFACTORING: TaskProfile(
        task_type=TaskType.REFACTORING,
        confidence=1.0,
        tools=_FILE_READ | _FILE_WRITE | _SHELL | _GIT,
        memory_keys=frozenset({"architecture", "coding_style"}),
        governance_categories=frozenset({"refactoring"}),
        load_full_prompt=False,
    ),
    TaskType.RESEARCH: TaskProfile(
        task_type=TaskType.RESEARCH,
        confidence=1.0,
        tools=_FILE_READ | _SHELL | _MEMORY | _AGENT,
        memory_keys=frozenset({"project_stack"}),
        governance_categories=frozenset({"research"}),
        load_full_prompt=False,
    ),
    TaskType.DEPLOYMENT: TaskProfile(
        task_type=TaskType.DEPLOYMENT,
        confidence=1.0,
        tools=_FILE_READ | _FILE_WRITE | _SHELL | _GIT,
        memory_keys=frozenset({"deployment_config", "infrastructure"}),
        governance_categories=frozenset({"deployment"}),
        load_full_prompt=False,
    ),
    TaskType.DOCUMENTATION: TaskProfile(
        task_type=TaskType.DOCUMENTATION,
        confidence=1.0,
        tools=_FILE_READ | _FILE_WRITE | _MEMORY,
        memory_keys=frozenset({"project_stack", "architecture"}),
        governance_categories=frozenset({"documentation"}),
        load_full_prompt=False,
    ),
    TaskType.CONVERSATION: TaskProfile(
        task_type=TaskType.CONVERSATION,
        confidence=1.0,
        tools=_MEMORY,
        memory_keys=frozenset(),
        governance_categories=frozenset({"conversation"}),
        load_full_prompt=False,
    ),
}
