"""Fork-subagent cache-sharing infrastructure.

This module implements the **byte-identical prefix** strategy borrowed
from claude-code's ``forkSubagent.ts``.  When multiple fork children are
spawned in parallel, all of them send the same API request prefix
(system prompt + history + assistant message + placeholder tool_results).
Only the final per-child directive differs.  This maximises prompt-cache
hits on providers that do byte-prefix matching (Anthropic).

For providers that lack prompt caching (OpenAI, vLLM, etc.) the fork
messages are still structurally correct — the children just won't benefit
from cache sharing.  No branching logic is needed at the callsite; the
design is **provider-agnostic by construction**.

Risk mitigations:
    - ``build_forked_messages()`` is a pure function (deep-copies input).
    - ``is_in_fork_child()`` detects the boilerplate tag to prevent
      infinite recursion, even though the ``agent`` tool is kept in the
      child's tool pool (for cache parity).
    - ``FORK_PLACEHOLDER_RESULT`` is a constant — no dynamic content
      that could break byte-identity.
"""
from __future__ import annotations

import copy
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Placeholder text used for every ``tool_result`` in the fork prefix.
#: **Must be identical across all fork children** for cache sharing.
FORK_PLACEHOLDER_RESULT: str = "Fork started \u2014 processing in background"

#: XML-style tag wrapped around the child boilerplate instruction block.
#: Used by ``is_in_fork_child()`` to detect recursion.
FORK_BOILERPLATE_TAG: str = "fork-boilerplate"

#: Prefix prepended to the per-child directive for easy log/grep.
FORK_DIRECTIVE_PREFIX: str = "[FORK-DIRECTIVE] "


# ---------------------------------------------------------------------------
# Cache key derivation (existing, kept for orchestrate_executor compat)
# ---------------------------------------------------------------------------

def derive_fork_key(parent_session_id: str, agent_role: str) -> str:
    """Stable cache key for child agents that inherit parent's prompt cache."""
    parent = parent_session_id or "root"
    role = agent_role or "anon"
    return f"{parent}:fork:{role}"


# ---------------------------------------------------------------------------
# Forked message construction
# ---------------------------------------------------------------------------

def build_forked_messages(
    directive: str,
    parent_assistant_msg: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build fork-child messages with a byte-identical prefix.

    All fork children receive:
        1. A deep-copy of the parent's last assistant message (keeping all
           thinking, text, and tool_use blocks intact).
        2. A single user message whose content is:
           - One ``tool_result`` per ``tool_use`` block, **all using the
             same placeholder text** (``FORK_PLACEHOLDER_RESULT``).
           - A final ``text`` block with the per-child directive (the
             **only** byte that differs between children).

    Returns ``[assistant_msg_copy, user_msg]``.  The caller prepends
    the shared conversation history to produce the full message array.

    Parameters
    ----------
    directive:
        Natural-language instruction specific to this child.
    parent_assistant_msg:
        The raw assistant message dict (Anthropic wire format) that
        contains the ``tool_use`` blocks triggering this fork.

    Returns
    -------
    list[dict]
        Two messages: cloned assistant + synthesised user.
    """
    # Deep-copy to avoid mutating the parent's message history
    full_assistant: dict[str, Any] = copy.deepcopy(parent_assistant_msg)

    # Collect tool_use blocks
    tool_uses: list[dict[str, Any]] = [
        b
        for b in parent_assistant_msg.get("content", [])
        if isinstance(b, dict) and b.get("type") == "tool_use"
    ]

    if not tool_uses:
        # Degenerate case: no tool_use → just a user message with directive
        return [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": build_child_message(directive)},
                ],
            }
        ]

    # Build identical placeholder tool_results
    tool_results: list[dict[str, Any]] = [
        {
            "type": "tool_result",
            "tool_use_id": tu["id"],
            "content": [
                {"type": "text", "text": FORK_PLACEHOLDER_RESULT},
            ],
        }
        for tu in tool_uses
    ]

    # Single user message: placeholders + per-child directive
    user_msg: dict[str, Any] = {
        "role": "user",
        "content": [
            *tool_results,
            {"type": "text", "text": build_child_message(directive)},
        ],
    }

    return [full_assistant, user_msg]


# ---------------------------------------------------------------------------
# Child boilerplate
# ---------------------------------------------------------------------------

def build_child_message(directive: str) -> str:
    """Construct the fork-child instruction block.

    The boilerplate enforces:
    - No recursive forking (rule 1)
    - No conversation / meta-commentary (rules 2-3)
    - Direct tool use followed by a single structured report (rules 4-8)
    - Structured output format for easy aggregation (rule 9)
    """
    return f"""<{FORK_BOILERPLATE_TAG}>
STOP. READ THIS FIRST.

You are a forked worker process. You are NOT the main agent.

RULES (non-negotiable):
1. Do NOT spawn sub-agents or delegate. Execute the task directly.
2. Do NOT converse, ask questions, or suggest next steps.
3. Do NOT editorialize or add meta-commentary.
4. USE your tools directly: bash, read_file, write_file, etc.
5. If you modify files, commit your changes. Include the commit hash.
6. Do NOT emit text between tool calls. Use tools silently, then report once.
7. Stay strictly within your directive's scope. Mention out-of-scope findings in one sentence at most.
8. Keep your report under 500 words unless the directive says otherwise.
9. Your response MUST begin with "Scope:". No preamble, no thinking-out-loud.

Output format:
  Scope: <echo back your assigned scope in one sentence>
  Result: <the answer or key findings, limited to the scope above>
  Key files: <relevant file paths>
  Files changed: <list with commit hash -- include only if you modified files>
  Issues: <list -- include only if there are issues to flag>
</{FORK_BOILERPLATE_TAG}>

{FORK_DIRECTIVE_PREFIX}{directive}"""


# ---------------------------------------------------------------------------
# Recursion guard
# ---------------------------------------------------------------------------

def is_in_fork_child(messages: list[dict[str, Any]]) -> bool:
    """Return True if the conversation history indicates we're inside a fork child.

    Fork children keep the ``agent`` tool in their tool pool (for cache
    parity), so we cannot rely on tool-pool filtering to prevent recursive
    forks.  Instead we detect the ``<fork-boilerplate>`` tag that
    ``build_child_message()`` injects into every child's user message.
    """
    tag = f"<{FORK_BOILERPLATE_TAG}>"
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            if tag in content:
                return True
        elif isinstance(content, list):
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "text"
                    and tag in block.get("text", "")
                ):
                    return True
    return False


# ---------------------------------------------------------------------------
# Worktree isolation notice
# ---------------------------------------------------------------------------

def build_worktree_notice(parent_cwd: str, worktree_cwd: str) -> str:
    """Context notice for fork children running in isolated git worktrees.

    Injected before the directive so the child knows to translate paths
    and re-read potentially stale files.
    """
    return (
        f"You've inherited the conversation context above from a parent "
        f"agent working in {parent_cwd}. You are operating in an isolated "
        f"git worktree at {worktree_cwd} \u2014 same repository, same "
        f"relative file structure, separate working copy. Paths in the "
        f"inherited context refer to the parent's working directory; "
        f"translate them to your worktree root. Re-read files before "
        f"editing if the parent may have modified them since they appear "
        f"in the context. Your changes stay in this worktree and will not "
        f"affect the parent's files."
    )
