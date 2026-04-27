"""Centralized registry of all built-in tools.

Adding a new tool? Add one entry here. No other wiring needed.

This module covers the collaborator-free "core" tool set — tools that
need no instance-scoped dependencies (MemoryStore, SwarmManager, etc.).
Instance-scoped tools (swarm, cron, task, IDE, LSP, memory, agent, etc.)
are registered separately in ``runtime_init.py`` because they require
runtime collaborators injected at startup.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_code.tools.base import Tool


def get_builtin_tools() -> dict[str, type["Tool"]]:
    """Return name -> Tool class mapping for all built-in core tools.

    Imports are inside the function to avoid circular imports and
    to keep module load time fast.
    """
    from llm_code.tools.bash import BashTool
    from llm_code.tools.edit_file import EditFileTool
    from llm_code.tools.git_tools import (
        GitBranchTool,
        GitCommitTool,
        GitDiffTool,
        GitLogTool,
        GitPushTool,
        GitStashTool,
        GitStatusTool,
    )
    from llm_code.tools.glob_search import GlobSearchTool
    from llm_code.tools.grep_search import GrepSearchTool
    from llm_code.tools.notebook_edit import NotebookEditTool
    from llm_code.tools.notebook_read import NotebookReadTool
    from llm_code.tools.read_file import ReadFileTool
    from llm_code.tools.rerank_tool import RerankTool
    from llm_code.tools.web_fetch import WebFetchTool
    from llm_code.tools.web_search import WebSearchTool
    from llm_code.tools.write_file import WriteFileTool

    return {
        # File I/O
        "read_file": ReadFileTool,
        "write_file": WriteFileTool,
        "edit_file": EditFileTool,
        # Shell
        "bash": BashTool,
        # Search
        "glob_search": GlobSearchTool,
        "grep_search": GrepSearchTool,
        # Notebooks
        "notebook_read": NotebookReadTool,
        "notebook_edit": NotebookEditTool,
        # Web
        "web_fetch": WebFetchTool,
        "web_search": WebSearchTool,
        # RAG (v2.8.0 M1)
        "rerank": RerankTool,
        # Git
        "git_status": GitStatusTool,
        "git_diff": GitDiffTool,
        "git_log": GitLogTool,
        "git_commit": GitCommitTool,
        "git_push": GitPushTool,
        "git_stash": GitStashTool,
        "git_branch": GitBranchTool,
    }
