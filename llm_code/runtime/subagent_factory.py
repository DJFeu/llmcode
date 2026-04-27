"""Subagent runtime factory.

Builds a child ConversationRuntime for a specific AgentRole. The child:

* Inherits the parent's provider, hook runner, prompt builder, permission
  policy, telemetry, config, and context (read-only sharing).
* Gets a filtered tool registry built from the role's allowed_tools whitelist
  via ToolRegistry.filtered.
* Gets a fresh Session so it does not see the parent's message history.
* Gets a _subagent_role attribute set on the runtime instance so downstream
  code (prompt builder, hook context) can detect it.

v16 M2 — when the parent runtime carries an
:class:`~llm_code.runtime.agent_memory.AgentMemoryStore` and the active
profile has ``agent_memory_enabled=True`` (default), three memory tools
are appended to the child registry. They share an ``AgentMemoryView``
keyed by the role name, so two consecutive subagents with the same role
can read each other's writes.

The factory does not start the runtime — it returns it ready to be driven by
run_turn(task) from the caller (which is AgentTool).
"""
from __future__ import annotations

import logging
from pathlib import Path

from llm_code.runtime.conversation import ConversationRuntime
from llm_code.runtime.session import Session
from llm_code.tools.agent_roles import BUILD_ROLE, AgentRole

_logger = logging.getLogger(__name__)

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

    # v16 M2: when agent memory is enabled, append the per-agent_id tool
    # surface so the subagent can read/write/list its memory cell.
    # Skipped on profiles that flip ``agent_memory_enabled`` off or when
    # the parent never set up an ``_agent_memory_store`` (e.g. legacy
    # initialiser paths). Failures here NEVER block the spawn — memory
    # is opt-in and a missing store collapses cleanly to the v2.5.5
    # behaviour.
    try:
        if _agent_memory_enabled(parent):
            store = _ensure_agent_memory_store(parent)
            view = store.view(effective_role.name or "anonymous")
            from llm_code.tools.agent_memory_tools import build_memory_tools

            for tool in build_memory_tools(view):
                # Respect the role's allowed_tools whitelist: if it's a
                # strict whitelist and ``memory_*`` is not listed, the
                # tool stays out so role authors keep full control.
                if (
                    effective_role.allowed_tools is not None
                    and tool.name not in effective_role.allowed_tools
                ):
                    continue
                # Don't trip the registry's "already registered" guard
                # if a custom user role registered its own ``memory_*``
                # tool; let the user's tool win.
                if child_registry.get(tool.name) is not None:
                    continue
                child_registry._tools[tool.name] = tool
            _logger.info(
                "agent_memory_injected agent_id=%r tools=%d",
                view.agent_id,
                3,
            )
    except Exception as exc:  # noqa: BLE001 — never crash a spawn
        _logger.warning("agent_memory injection failed: %r", exc)

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

    # G1: subagents share the parent's SandboxLifecycleManager so any
    # backend a sub-agent opens gets closed when the parent session
    # ends. getattr() guards against parents that never touched a
    # backend (no lifecycle yet) — the property stays None and the
    # child can lazily create its own later if needed.
    parent_lifecycle = getattr(parent, "_sandbox_lifecycle", None)
    if parent_lifecycle is not None:
        child._sandbox_lifecycle = parent_lifecycle  # type: ignore[attr-defined]

    return child


# ---------------------------------------------------------------------------
# v16 M2 helpers
# ---------------------------------------------------------------------------


def _agent_memory_enabled(parent: ConversationRuntime) -> bool:
    """Return True when the parent's profile opts into agent memory.

    Reads through ``parent._config.profile`` if present (the rich
    profile objects carry the flag); otherwise falls back to the
    config dict (``agent_memory_enabled`` flat field) and finally to
    the safe default of ``True``. Never raises.
    """
    cfg = getattr(parent, "_config", None)
    profile = getattr(cfg, "profile", None) if cfg is not None else None
    if profile is not None and hasattr(profile, "agent_memory_enabled"):
        return bool(profile.agent_memory_enabled)
    if cfg is not None and hasattr(cfg, "agent_memory_enabled"):
        return bool(cfg.agent_memory_enabled)
    return True


def _ensure_agent_memory_store(parent: ConversationRuntime):
    """Return the parent's :class:`AgentMemoryStore`, creating it on demand.

    The store is attached to the parent runtime as ``_agent_memory_store``
    so every child subagent shares a single store for the session.
    """
    from llm_code.runtime.agent_memory import AgentMemoryStore

    store = getattr(parent, "_agent_memory_store", None)
    if store is None:
        store = AgentMemoryStore()
        parent._agent_memory_store = store  # type: ignore[attr-defined]
    return store
