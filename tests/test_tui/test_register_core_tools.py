"""Verify LLMCodeTUI._register_core_tools_into registers the same
collaborator-free core tool set the TUI boot path has always registered,
and is callable from headless contexts (like ``run_quick_mode``) that
don't build a full TUI instance."""
from __future__ import annotations

from llm_code.runtime.config import RuntimeConfig
from llm_code.tools.registry import ToolRegistry
from llm_code.tui.app import LLMCodeTUI


EXPECTED_CORE_TOOLS = {
    # file + shell
    "read_file",
    "write_file",
    "edit_file",
    "bash",
    "glob_search",
    "grep_search",
    "notebook_read",
    "notebook_edit",
    "web_fetch",
    "web_search",
    # git
    "git_status",
    "git_diff",
    "git_log",
    "git_commit",
    "git_push",
    "git_stash",
    "git_branch",
}


def test_register_core_tools_into_populates_registry() -> None:
    registry = ToolRegistry()
    LLMCodeTUI._register_core_tools_into(registry, RuntimeConfig())
    registered = {t.name for t in registry.all_tools()}
    missing = EXPECTED_CORE_TOOLS - registered
    assert not missing, f"core tools missing from registry: {missing}"
