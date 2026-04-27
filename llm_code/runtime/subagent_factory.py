"""Subagent runtime factory.

v16 M7 — supports per-role tool subsets via wildcard expansion (the
``tools:`` frontmatter list), prebuilt policy presets (``tool_policy:``),
and per-tool args allowlists (``bash:git status,git diff``). Inline
MCP servers declared on a role spawn as subprocess.Popen instances and
tear down via SIGTERM (10s grace) → SIGKILL when the subagent exits.

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
import signal
import subprocess
import time
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

    v16 M7 wiring (no behaviour change for wave-1 roles):

    * When a role declares ``tool_specs`` (frontmatter ``tools:``
      with wildcards / args allowlists) or ``tool_policy`` (one of
      the prebuilt policies), :func:`runtime.tool_policy.resolve_tool_subset`
      expands them against the parent's tool surface and the
      result drives the registry filter.
    * Inline MCP servers declared on the role (``mcp_servers:``)
      spawn via :class:`InlineMcpRegistry` and tear down when the
      child runtime is shut down.
    """
    effective_role = role if role is not None else BUILD_ROLE

    # v16 M7 — expand wildcards / policy presets BEFORE the registry
    # filter runs so the rest of the multi-stage filter applies to
    # the resolved name set. Per-tool args allowlists are gathered
    # alongside; they get enforced at call time via tool wrappers.
    effective_allowed = effective_role.allowed_tools
    per_tool_args: dict[str, tuple[str, ...]] = {}
    has_dynamic_policy = bool(
        effective_role.tool_specs or effective_role.tool_policy
    )
    if has_dynamic_policy:
        from llm_code.runtime.tool_policy import resolve_tool_subset

        parent_names = frozenset(parent._tool_registry._tools.keys())
        resolved_set, per_tool_args = resolve_tool_subset(
            parent_names,
            explicit_tools=effective_role.tool_specs,
            policy=effective_role.tool_policy or None,
        )
        effective_allowed = resolved_set

    # Multi-stage tool filtering:
    #   Stage 1: MCP tools bypass all checks
    #   Stage 2: (plan-mode bypass — handled elsewhere)
    #   Stage 3: ALL_AGENT_DISALLOWED global deny-list
    #   Stage 4: CUSTOM_AGENT_DISALLOWED for user-defined agents
    #   Stage 5: ASYNC_AGENT_ALLOWED positive filter (background agents)
    #   + effective_allowed whitelist + role.disallowed_tools deny
    child_registry = parent._tool_registry.filtered(
        effective_allowed,
        disallowed=effective_role.disallowed_tools,
        is_builtin=effective_role.is_builtin,
        is_async=effective_role.is_async,
    )

    # v16 M7 — wrap each tool that has an args allowlist so calls
    # with mismatched args are rejected at dispatch time. Tools
    # without an allowlist pass through unchanged.
    if per_tool_args:
        from llm_code.runtime.tool_policy import args_allowlist_check

        for tool_name, allowlist in per_tool_args.items():
            tool = child_registry._tools.get(tool_name)
            if tool is None:
                continue
            child_registry._tools[tool_name] = _ArgsAllowlistTool(
                tool, allowlist, args_allowlist_check,
            )

    # v16 M7 — spawn inline MCP servers declared on the role. Each
    # server is wrapped in a process registry attached to the child
    # runtime so ``runtime.shutdown()`` triggers the SIGTERM/SIGKILL
    # teardown chain. Spawn failures are logged but never block the
    # subagent boot — a missing inline MCP shouldn't crash the user
    # mid-turn.
    inline_mcp_registry: InlineMcpRegistry | None = None
    if effective_role.inline_mcp_servers:
        inline_mcp_registry = InlineMcpRegistry()
        for name, command, args in effective_role.inline_mcp_servers:
            try:
                inline_mcp_registry.spawn(name, command, args)
            except Exception as exc:  # noqa: BLE001 — log + continue
                _logger.warning(
                    "inline MCP server %r failed to spawn: %r", name, exc,
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

    # v16 M7 — attach the inline MCP registry to the runtime + extend
    # ``shutdown`` so the subprocess teardown chain fires when the
    # subagent exits. Wrapped in a try because some test paths build
    # a runtime without a real shutdown method.
    if inline_mcp_registry is not None:
        child._inline_mcp_registry = inline_mcp_registry  # type: ignore[attr-defined]
        original_shutdown = getattr(child, "shutdown", None)

        def _shutdown_with_mcp() -> None:
            try:
                inline_mcp_registry.shutdown_all()
            finally:
                if callable(original_shutdown):
                    try:
                        original_shutdown()
                    except Exception as exc:  # noqa: BLE001 — best-effort cleanup
                        _logger.warning(
                            "subagent shutdown after MCP teardown raised: %r", exc,
                        )

        child.shutdown = _shutdown_with_mcp  # type: ignore[assignment]

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


# ---------------------------------------------------------------------------
# v16 M7 — args allowlist + inline MCP runtime
# ---------------------------------------------------------------------------


class _ArgsAllowlistTool:
    """Wraps a Tool so calls with non-allowlisted args are rejected.

    The wrapper preserves the underlying tool's name + definition so
    everything the LLM sees is unchanged. Only the dispatched call's
    args are gated. Both sync (:meth:`execute`) and async
    (:meth:`execute_async`) entry points are intercepted because tool
    dispatch in the runtime can pick either path.

    Args allowlist semantics live in
    :func:`runtime.tool_policy.args_allowlist_check` — empty allowlist
    is a passthrough.
    """

    def __init__(
        self,
        underlying,
        allowlist: tuple[str, ...],
        check,
    ) -> None:
        self._underlying = underlying
        self._allowlist = allowlist
        self._check = check
        # Mirror common Tool attributes so the registry surface matches.
        self.name = getattr(underlying, "name", "")
        self.description = getattr(underlying, "description", "")

    def __getattr__(self, item: str):
        # Fall back to the wrapped tool for anything we don't override.
        return getattr(self._underlying, item)

    def _denied_result(self):
        from llm_code.tools.base import ToolResult

        allowed = ", ".join(self._allowlist) if self._allowlist else ""
        return ToolResult(
            output=(
                f"{self.name}: arguments not permitted by role policy. "
                f"Allowed prefixes: {allowed}"
            ),
            is_error=True,
        )

    def execute(self, args: dict):
        if not self._check(self.name, args, self._allowlist):
            return self._denied_result()
        return self._underlying.execute(args)

    async def execute_async(self, args: dict):
        if not self._check(self.name, args, self._allowlist):
            return self._denied_result()
        return await self._underlying.execute_async(args)

    # Some legacy tests / call sites use ``call`` (kwargs-based) as
    # the entry point. Keep that surface working.
    async def call(self, **kwargs):
        if not self._check(self.name, kwargs, self._allowlist):
            return self._denied_result()
        if hasattr(self._underlying, "call"):
            return await self._underlying.call(**kwargs)
        return await self._underlying.execute_async(kwargs)

    def to_definition(self):
        # Pass through the schema unchanged so the LLM sees the same
        # tool signature.
        if hasattr(self._underlying, "to_definition"):
            return self._underlying.to_definition()
        return None


class InlineMcpRegistry:
    """Tracks inline MCP server subprocesses spawned for a subagent.

    The registry's only job is lifecycle: spawn / track PID / tear
    down with SIGTERM (10s grace) → SIGKILL on shutdown. The actual
    MCP protocol traffic flows through the child runtime's existing
    MCP manager, which is unchanged for wave-1 inline MCP roles
    (none) and wave-2 MCP-aware roles (M7 spec §3.7).

    Spawn failures are logged + raised so the subagent factory can
    continue without the failing server.
    """

    _SIGTERM_GRACE_SECONDS = 10.0

    def __init__(self) -> None:
        self._processes: list[tuple[str, "_MCPProc"]] = []

    def spawn(self, name: str, command: str, args: tuple[str, ...]) -> None:
        """Spawn ``command`` + ``args`` as a tracked subprocess.

        The MCP stdio handshake is the consumer's responsibility (the
        runtime's MCP manager). This method's contract is "process
        is alive after spawn or we raise".
        """
        try:
            proc = subprocess.Popen(  # noqa: S603 — command + args are role-defined
                [command, *args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except (FileNotFoundError, OSError) as exc:
            raise RuntimeError(f"cannot spawn MCP server {name!r}: {exc}") from exc
        self._processes.append((name, _MCPProc(proc)))
        _logger.info("inline_mcp_spawned name=%s pid=%d", name, proc.pid)

    def shutdown_all(self) -> None:
        """Terminate every tracked subprocess.

        SIGTERM first; if a process is still alive after the grace
        period, send SIGKILL. Errors during teardown are logged but
        never raised so a stuck child can't break shutdown of the
        whole session.
        """
        if not self._processes:
            return

        # First pass: SIGTERM every alive child.
        for _name, proc_wrapper in self._processes:
            proc = proc_wrapper.proc
            if proc.poll() is None:
                try:
                    proc.send_signal(signal.SIGTERM)
                except (OSError, ProcessLookupError):
                    pass

        # Wait up to the grace period, polling each.
        deadline = time.monotonic() + self._SIGTERM_GRACE_SECONDS
        for _name, proc_wrapper in self._processes:
            proc = proc_wrapper.proc
            remaining = max(0.0, deadline - time.monotonic())
            try:
                proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                pass

        # Second pass: SIGKILL anything still alive.
        for name, proc_wrapper in self._processes:
            proc = proc_wrapper.proc
            if proc.poll() is None:
                try:
                    proc.kill()
                except (OSError, ProcessLookupError):
                    pass
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    pass
                except Exception:  # noqa: BLE001
                    pass
            _logger.info("inline_mcp_terminated name=%s exit=%s", name, proc.returncode)
        self._processes.clear()


class _MCPProc:
    """Bag wrapper so the registry can keep extra metadata next to the proc."""

    def __init__(self, proc) -> None:
        self.proc = proc
        self.pid = proc.pid
