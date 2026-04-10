"""Per-turn tool visibility control for local models.

Borrowed from Codex CLI's ``model_visible_specs`` vs ``specs`` pattern.

Problem: local models (Qwen INT4, DeepSeek, etc.) have limited context
windows.  Sending 30+ tool definitions every turn wastes ~2-3K tokens
on schema the model won't use.

Solution: classify user intent via keywords → send only the relevant
tool group + always-visible core tools.  If the classifier is wrong,
``bash`` is always visible as a universal fallback.

Design:
    - ``INTENT_TOOL_GROUPS``: keyword → tool set mapping (no LLM call)
    - ``ALWAYS_VISIBLE``: core tools always sent regardless of intent
    - ``classify_intents()``: returns ALL matching intents (union)
    - ``visible_tools_for_turn()``: pure function, caller uses result
      to filter ``registry.definitions()``

Risk mitigations:
    - Only active when ``is_local=True`` — cloud APIs get full tool set
    - ``bash`` in ALWAYS_VISIBLE = universal fallback
    - Multi-intent union = "改 README 然後 push" hits both code_edit + git
    - ``max_tools`` cap is a soft limit (ALWAYS_VISIBLE always included)
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Intent → tool group mapping
# ---------------------------------------------------------------------------

INTENT_TOOL_GROUPS: dict[str, frozenset[str]] = {
    "code_edit": frozenset({
        "read_file", "write_file", "edit_file", "multi_edit",
        "glob_search", "grep_search", "bash",
        "git_status", "git_diff",
        "lsp_goto_definition", "lsp_find_references", "lsp_hover",
        "lsp_diagnostics", "lsp_document_symbol",
    }),
    "search": frozenset({
        "read_file", "glob_search", "grep_search",
        "web_search", "web_fetch",
        "lsp_workspace_symbol", "lsp_find_references",
    }),
    "git": frozenset({
        "git_status", "git_diff", "git_log",
        "git_commit", "git_push", "git_stash", "git_branch",
        "bash", "read_file",
    }),
    "notebook": frozenset({
        "notebook_read", "notebook_edit",
        "read_file", "write_file", "bash",
    }),
    "web": frozenset({
        "web_search", "web_fetch", "bash",
    }),
    "test": frozenset({
        "bash", "read_file", "glob_search", "grep_search",
        "git_status", "git_diff",
    }),
    "plan": frozenset({
        "read_file", "glob_search", "grep_search",
        "git_status", "git_diff", "git_log",
        "web_search", "web_fetch",
    }),
}

# Intent detection keywords (compiled once)
_INTENT_KEYWORDS: dict[str, re.Pattern[str]] = {
    "code_edit": re.compile(
        r"(?:edit|modify|change|update|fix|refactor|add|create|write|implement|build"
        r"|新增|修改|修復|重構|建立|實作|改|寫|加)",
        re.IGNORECASE,
    ),
    "search": re.compile(
        r"(?:search|find|look\s*for|where\s+is|locate|grep|查找|搜尋|找|在哪)",
        re.IGNORECASE,
    ),
    "git": re.compile(
        r"(?:commit|push|pull|merge|branch|stash|rebase|cherry.?pick|tag|提交|推送|分支)",
        re.IGNORECASE,
    ),
    "notebook": re.compile(
        r"(?:notebook|jupyter|\.ipynb|cell)",
        re.IGNORECASE,
    ),
    "web": re.compile(
        r"(?:search\s+(?:the\s+)?web|google|fetch\s+url|http|搜尋網路|上網|網頁)",
        re.IGNORECASE,
    ),
    "test": re.compile(
        r"(?:test|pytest|unittest|spec|coverage|run\s+tests|測試|跑測試)",
        re.IGNORECASE,
    ),
    "plan": re.compile(
        r"(?:plan|analyze|review|explain|describe|what\s+does|how\s+does"
        r"|規劃|分析|說明|解釋|看看)",
        re.IGNORECASE,
    ),
}

# Tools always visible regardless of intent
ALWAYS_VISIBLE: frozenset[str] = frozenset({
    "read_file",
    "bash",
    "glob_search",
    "grep_search",
})

# MCP tool prefix — always pass through
_MCP_PREFIX: str = "mcp__"


# ---------------------------------------------------------------------------
# Intent classification
# ---------------------------------------------------------------------------

def classify_intents(user_message: str) -> list[str]:
    """Classify user message into zero or more intent categories.

    Returns all matching intents (not just the first).
    This is a keyword heuristic — no LLM call.
    """
    matched: list[str] = []
    for intent, pattern in _INTENT_KEYWORDS.items():
        if pattern.search(user_message):
            matched.append(intent)
    return matched


# ---------------------------------------------------------------------------
# Tool visibility filter
# ---------------------------------------------------------------------------

def visible_tools_for_turn(
    all_tool_names: frozenset[str],
    user_message: str,
    *,
    max_tools: int = 20,
) -> frozenset[str]:
    """Return the tool subset visible to the model for this turn.

    Parameters
    ----------
    all_tool_names:
        Every tool registered in the parent registry.
    user_message:
        The user's latest message (used for intent classification).
    max_tools:
        Soft cap on visible tools (ALWAYS_VISIBLE + MCP always included).

    Returns
    -------
    frozenset[str]
        Tool names to include in the API request's tool definitions.
    """
    # Start with always-visible tools
    visible: set[str] = set()
    for name in all_tool_names:
        if name in ALWAYS_VISIBLE or name.startswith(_MCP_PREFIX):
            visible.add(name)

    # Add tools from all matching intent groups
    intents = classify_intents(user_message)
    for intent in intents:
        group = INTENT_TOOL_GROUPS.get(intent, frozenset())
        visible.update(name for name in group if name in all_tool_names)

    # If no intent matched, include code_edit as default (most common)
    if not intents:
        default = INTENT_TOOL_GROUPS["code_edit"]
        visible.update(name for name in default if name in all_tool_names)

    # Soft cap: if over max, keep ALWAYS_VISIBLE + MCP + trim the rest
    if len(visible) > max_tools:
        core = {n for n in visible if n in ALWAYS_VISIBLE or n.startswith(_MCP_PREFIX)}
        extra = sorted(visible - core)[:max_tools - len(core)]
        visible = core | set(extra)

    return frozenset(visible)
