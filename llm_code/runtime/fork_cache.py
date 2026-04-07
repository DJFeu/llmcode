"""Cache key derivation for forked subagents.

When spawning a sub-agent (swarm member, persona executor, classifier),
inherit the parent's prompt cache key so the API can reuse the cached
system prompt instead of paying full input-token cost again.

For Anthropic this enables ~90% cost savings on subagent calls via
``cache_control: {type: "ephemeral"}`` on the system prompt block.
For non-Anthropic providers it is a structurally-correct no-op.
"""
from __future__ import annotations


def derive_fork_key(parent_session_id: str, agent_role: str) -> str:
    """Stable cache key for child agents that inherit parent's prompt cache."""
    parent = parent_session_id or "root"
    role = agent_role or "anon"
    return f"{parent}:fork:{role}"
