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

_ANTI_RECURSION = (
    "\n\nIMPORTANT: You are a sub-agent. Do NOT spawn further sub-agents or "
    "delegate work. Execute the task directly. If you cannot complete it, "
    "report back with what you found — do not attempt to fork or delegate."
)


def make_subagent_runtime(
    parent: ConversationRuntime,
    role: AgentRole | None,
    model: str | None = None,
) -> ConversationRuntime:
    """Build a sub-ConversationRuntime for *role* under *parent*.

    *model* overrides the parent's active model for this sub-agent.
    Pass None to inherit the parent default.
    """
    effective_role = role if role is not None else BUILD_ROLE

    # Multi-stage tool filtering:
    #   Stage 1: MCP tools bypass all checks
    #   Stage 2: (plan-mode bypass — handled elsewhere)
    #   Stage 3: ALL_AGENT_DISALLOWED global deny-list
    #   Stage 4: CUSTOM_AGENT_DISALLOWED for user-defined agents
    #   Stage 5: ASYNC_AGENT_ALLOWED positive filter (background agents)
    #   + role.allowed_tools whitelist + role.disallowed_tools deny
    child_registry = parent._tool_registry.filtered(
        effective_role.allowed_tools,
        disallowed=effective_role.disallowed_tools,
        is_builtin=effective_role.is_builtin,
        is_async=effective_role.is_async,
    )

    # Recursion-depth fix: the inherited "agent" tool instance is shared with
    # the parent, so its _current_depth is still the parent's. Replace it with
    # a fresh AgentTool whose depth is parent_depth + 1, preserving max_depth
    # and the runtime_factory closure.
    parent_agent_tool = child_registry.get("agent")
    if parent_agent_tool is not None:
        from llm_code.tools.agent import AgentTool

        if isinstance(parent_agent_tool, AgentTool):
            child_agent_tool = AgentTool(
                runtime_factory=parent_agent_tool._runtime_factory,
                max_depth=parent_agent_tool._max_depth,
                current_depth=parent_agent_tool._current_depth + 1,
            )
            # In-place replacement on the child registry only — parent
            # registry is untouched because filtered() returned a new dict.
            child_registry._tools["agent"] = child_agent_tool

    # Fresh session — no parent history.
    project_path = getattr(parent._context, "project_path", None)
    if not isinstance(project_path, Path):
        project_path = Path.cwd()
    child_session = Session.create(project_path)

    # Apply model override if specified
    child_config = parent._config
    if model:
        import dataclasses as _dc
        child_config = _dc.replace(parent._config, model=model)

    child = ConversationRuntime(
        provider=parent._provider,
        tool_registry=child_registry,
        permission_policy=parent._permissions,
        hook_runner=parent._hooks,
        prompt_builder=parent._prompt_builder,
        config=child_config,
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
    child._subagent_system_suffix = _ANTI_RECURSION  # type: ignore[attr-defined]
    return child
