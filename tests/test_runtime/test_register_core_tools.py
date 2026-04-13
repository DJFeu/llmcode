"""Verify runtime.core_tools.register_core_tools registers the same
collaborator-free core tool set the REPL boot path has always used,
and is callable from headless contexts (like ``run_quick_mode``) that
don't build a full AppState.

Relocated from ``tests/test_tui/test_register_core_tools.py`` in M11.4:
the helper moved from ``tui/app.py`` (as ``_register_core_tools_into``)
to ``runtime/core_tools.py`` (as ``register_core_tools``) during M11.1,
and the test tree follows.
"""
from __future__ import annotations

from llm_code.runtime.config import RuntimeConfig
from llm_code.runtime.core_tools import register_core_tools
from llm_code.tools.registry import ToolRegistry


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


def test_register_core_tools_populates_registry() -> None:
    registry = ToolRegistry()
    register_core_tools(registry, RuntimeConfig())
    registered = {t.name for t in registry.all_tools()}
    missing = EXPECTED_CORE_TOOLS - registered
    assert not missing, f"core tools missing from registry: {missing}"
