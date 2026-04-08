"""Subagent runtime factory.

Builds a child ConversationRuntime for a specific AgentRole. The child:

* Inherits the parent's provider, hook runner, prompt builder, permission
  policy, telemetry, config, and context (read-only sharing).
* Gets a filtered tool registry built from the role's allowed_tools whitelist
  via ToolRegistry.filtered.
* Gets a fresh Session so it does not see the parent's message history.
* Gets a _subagent_role attribute set on the runtime instance so downstream
  code (prompt builder, hook context) can detect it.

The factory does not start the runtime — it returns it ready to be driven by
run_turn(task) from the caller (which is AgentTool).
"""
from __future__ import annotations

from pathlib import Path

from llm_code.runtime.conversation import ConversationRuntime
from llm_code.runtime.session import Session
from llm_code.tools.agent_roles import BUILD_ROLE, AgentRole


def make_subagent_runtime(
    parent: ConversationRuntime,
    role: AgentRole | None,
    model: str | None = None,
) -> ConversationRuntime:
    """Build a sub-ConversationRuntime for *role* under *parent*.

    *model* is currently a placeholder for future per-role model routing —
    accepted but not yet wired through. Pass None for the parent default.
    """
    effective_role = role if role is not None else BUILD_ROLE

    # Whitelist filter (empty whitelist = unrestricted copy).
    child_registry = parent._tool_registry.filtered(effective_role.allowed_tools)

    # Fresh session — no parent history.
    project_path = getattr(parent._context, "project_path", None)
    if not isinstance(project_path, Path):
        project_path = Path.cwd()
    child_session = Session.create(project_path)

    child = ConversationRuntime(
        provider=parent._provider,
        tool_registry=child_registry,
        permission_policy=parent._permissions,
        hook_runner=parent._hooks,
        prompt_builder=parent._prompt_builder,
        config=parent._config,
        session=child_session,
        context=parent._context,
        checkpoint_manager=getattr(parent, "_checkpoint_mgr", None),
        token_budget=getattr(parent, "_token_budget", None),
        vcr_recorder=getattr(parent, "_vcr_recorder", None),
        deferred_tool_manager=getattr(parent, "_deferred_tool_manager", None),
        telemetry=getattr(parent, "_telemetry", None),
        recovery_checkpoint=getattr(parent, "_recovery_checkpoint", None),
        cost_tracker=getattr(parent, "_cost_tracker", None),
        skills=getattr(parent, "_skills", None),
        mcp_manager=getattr(parent, "_mcp_manager", None),
        memory_store=getattr(parent, "_memory_store", None),
        task_manager=getattr(parent, "_task_manager", None),
        project_index=getattr(parent, "_project_index", None),
        lsp_manager=getattr(parent, "_lsp_manager", None),
        typed_memory_store=getattr(parent, "_typed_memory_store", None),
    )

    # Tag the runtime with its role so downstream observers can react.
    child._subagent_role = effective_role  # type: ignore[attr-defined]
    return child
