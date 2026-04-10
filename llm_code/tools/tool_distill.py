"""Tool description distillation for context-constrained models.

Borrowed from Gemini CLI's tool distillation service.

When context budget is tight (local models), tool descriptions can be
shortened from full documentation to one-line summaries.  This saves
~1-2K tokens on a 30-tool registry.

Design:
    - ``COMPACT_DESCRIPTIONS``: hand-written one-liners per tool
    - ``distill_definitions()``: pure function, returns new tuple
    - Only replaces description — schema is untouched (model still
      knows the parameter format)
    - Unknown tools keep their original description
"""
from __future__ import annotations

from llm_code.api.types import ToolDefinition

# Hand-written compact descriptions.
# These are NOT auto-generated — each is carefully written to preserve
# the essential action in minimal tokens.
COMPACT_DESCRIPTIONS: dict[str, str] = {
    # File I/O
    "read_file": "Read file content by path",
    "write_file": "Write/create a file",
    "edit_file": "Edit file via search & replace",
    "multi_edit": "Multiple edits in one file",
    # Search
    "glob_search": "Find files by glob pattern",
    "grep_search": "Search file contents by regex",
    "tool_search": "Find deferred tools by keyword",
    # Shell
    "bash": "Run a shell command",
    # Web
    "web_search": "Search the web",
    "web_fetch": "Fetch URL content",
    # Git
    "git_status": "Show git status",
    "git_diff": "Show git diff",
    "git_log": "Show git log",
    "git_commit": "Create git commit",
    "git_push": "Push to remote",
    "git_stash": "Stash changes",
    "git_branch": "List/create branches",
    # Notebook
    "notebook_read": "Read notebook cells",
    "notebook_edit": "Edit notebook cells",
    # LSP
    "lsp_hover": "Show type info at position",
    "lsp_goto_definition": "Jump to definition",
    "lsp_find_references": "Find all references",
    "lsp_diagnostics": "Show diagnostics/errors",
    "lsp_document_symbol": "List symbols in file",
    "lsp_workspace_symbol": "Search symbols in workspace",
    "lsp_go_to_implementation": "Jump to implementation",
    "lsp_call_hierarchy": "Show call hierarchy",
    # Task lifecycle
    "task_plan": "Create a task plan",
    "task_verify": "Verify task completion",
    "task_close": "Close a task",
    # Agent
    "agent": "Spawn a sub-agent",
    # Scheduling
    "cron_create": "Schedule a recurring task",
    "cron_list": "List scheduled tasks",
    "cron_delete": "Delete a scheduled task",
    # IDE
    "ide_open": "Open file in IDE",
    "ide_diagnostics": "Get IDE diagnostics",
    "ide_selection": "Get IDE selection",
    # Computer use
    "screenshot": "Take a screenshot",
    "mouse_click": "Click at coordinates",
    "keyboard_type": "Type text",
    # Skills
    "skill_load": "Load a skill",
    # Swarm
    "swarm_create": "Create agent swarm",
    "swarm_list": "List active swarms",
    "swarm_message": "Send message to swarm",
    "swarm_delete": "Delete a swarm",
    "coordinator": "Coordinate multi-agent task",
    # Plan mode
    "enter_plan_mode": "Switch to plan mode",
    "exit_plan_mode": "Exit plan mode",
}


def distill_definitions(
    defs: tuple[ToolDefinition, ...],
    *,
    compact: bool = False,
) -> tuple[ToolDefinition, ...]:
    """Return tool definitions, optionally with compact descriptions.

    When ``compact=True``, tools with entries in ``COMPACT_DESCRIPTIONS``
    get their description replaced with the short version.  Schema
    (input_schema) is preserved exactly — the model can still call tools
    correctly.

    Parameters
    ----------
    defs:
        Original tool definitions from the registry.
    compact:
        If True, use compact descriptions where available.

    Returns
    -------
    tuple[ToolDefinition, ...]
        Possibly-shortened definitions (new tuple, originals untouched).
    """
    if not compact:
        return defs

    result: list[ToolDefinition] = []
    for d in defs:
        short = COMPACT_DESCRIPTIONS.get(d.name)
        if short:
            result.append(ToolDefinition(
                name=d.name,
                description=short,
                input_schema=d.input_schema,
            ))
        else:
            result.append(d)

    return tuple(result)
