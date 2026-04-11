"""AppState — view-agnostic application state container.

M10.3 deliverable. Lifts the ~30 state fields and subsystem references
that v1.23.1 kept on ``LLMCodeTUI`` up into a standalone dataclass so
the new REPL backend (M11) can build them without the Textual TUI.

Design decisions (see ``docs/superpowers/plans/2026-04-12-m10-redesign.md``):

1. **Dataclass, not Protocol.** The M11 CLI entry needs to instantiate
   AppState standalone, which requires a concrete class.

2. **``from_config`` factory mirrors ``RuntimeInitializer.initialize()``
   line-for-line.** Same subsystem order, same optional-dep try/except
   resilience, same tool registration sequence. During M10 both code
   paths coexist: ``tui/runtime_init.py`` becomes a thin adapter that
   delegates to ``AppState.from_config`` and copies the resulting
   fields back onto ``LLMCodeTUI``. M11 deletes the adapter.

3. **TUI-specific wiring stays out.** The factory does *not* build a
   ``TextualDialogs`` instance, does *not* install the MCP approval
   event sink, does *not* start the voice monitor Textual Timer. Those
   are TUI-specific concerns — the adapter in ``runtime_init.py``
   adds them on top of the returned AppState for the legacy TUI, and
   M11's REPL wiring adds its own equivalents.

4. **Live fields live on AppState, not on the view.** ``input_tokens``,
   ``output_tokens``, ``last_stop_reason``, ``plan_mode``,
   ``voice_active``, ``voice_recorder``, etc. are mutated during a
   session by ViewStreamRenderer and CommandDispatcher; both will hold
   an AppState reference.

Things deliberately NOT on AppState (and why):

- ``interrupt_pending`` / ``last_interrupt_time`` — v1.x double-Ctrl+C
  throttle, TUI-specific. The new REPL has its own exit flow.
- ``mcp_approval_pending`` / ``mcp_approval_widget`` — Textual widget
  bookkeeping. Approval UI is M8 ``DialogPopover`` in v2.0.0.
- ``voice_monitor_timer`` — a Textual ``set_interval`` handle. The new
  REPL's polling happens inside ``PollingRecorderAdapter`` (M9.5).
- ``dialogs`` — TUI-only Textual dialog system. Runtime creates a
  ``HeadlessDialogs`` fallback when ``dialogs=None``.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path
from typing import Any, Callable, Optional

from llm_code.logging import get_logger

logger = get_logger(__name__)


@dataclass
class AppState:
    """View-agnostic container for all application state.

    The factory :meth:`from_config` builds every subsystem field from
    a ``RuntimeConfig``; callers that already have subsystems in hand
    (tests, the legacy TUI adapter) can also construct an instance
    directly and set fields individually.

    Fields are grouped by role:

    * **Input / config** — set by the factory caller.
    * **Subsystems** — built by the factory (may be ``None`` when the
      optional dependency is missing or the feature is disabled).
    * **Live mutation state** — mutated during a session by renderers
      and command handlers; reset when a new session starts.
    """

    # ── Input / config ────────────────────────────────────────────
    config: Any = None
    cwd: Path = field(default_factory=Path.cwd)
    budget: Optional[int] = None
    initial_mode: str = "workspace_write"

    # ── Subsystems built by from_config ───────────────────────────
    runtime: Any = None
    cost_tracker: Any = None
    tool_reg: Any = None
    deferred_tool_manager: Any = None
    checkpoint_mgr: Any = None
    recovery_checkpoint: Any = None
    token_budget: Any = None
    skills: Any = None
    memory: Any = None
    typed_memory: Any = None
    cron_storage: Any = None
    swarm_manager: Any = None
    task_manager: Any = None
    ide_bridge: Any = None
    lsp_manager: Any = None
    project_index: Any = None
    user_agent_roles: dict = field(default_factory=dict)
    telemetry: Any = None
    mcp_manager: Any = None
    # TUI-only fallback — REPL backend leaves this None and runtime
    # uses HeadlessDialogs.
    dialogs: Any = None

    # ── Live mutation state ───────────────────────────────────────
    input_tokens: int = 0
    output_tokens: int = 0
    last_stop_reason: str = "unknown"
    pending_images: list = field(default_factory=list)
    loaded_plugins: dict = field(default_factory=dict)
    plan_mode: bool = False
    voice_active: bool = False
    voice_recorder: Any = None
    voice_stt: Any = None
    analysis_context: Optional[str] = None
    context_warned: bool = False
    permission_pending: bool = False

    @classmethod
    def from_config(
        cls,
        config: Any,
        cwd: Optional[Path] = None,
        *,
        budget: Optional[int] = None,
        initial_mode: str = "workspace_write",
        register_core_tools: Optional[Callable[[Any, Any], None]] = None,
    ) -> "AppState":
        """Build an AppState from a RuntimeConfig, matching the legacy
        ``RuntimeInitializer.initialize()`` subsystem graph.

        Returns a fully-populated ``AppState`` instance. When ``config``
        is ``None``, returns an empty shell (matching the legacy
        "No config provided; runtime will not be initialized." path).

        ``register_core_tools`` is an optional injection hook for the
        free function that registers collaborator-free core tools on
        the tool registry. When ``None``, the factory imports the
        current implementation from ``llm_code.tui.app`` — that import
        stays in place until M11 deletes ``tui/`` and relocates the
        helper to a neutral module.
        """
        state = cls(
            config=config,
            cwd=cwd or Path.cwd(),
            budget=budget,
            initial_mode=initial_mode,
        )

        if config is None:
            logger.warning(
                "AppState.from_config: no config provided; runtime will not be initialized."
            )
            return state

        # Imports are deferred so building an empty shell (config=None)
        # stays cheap and so circular-import risks with modules that
        # pull in runtime transitively are limited to the happy path.
        from llm_code.api.client import ProviderClient
        from llm_code.runtime.cost_tracker import CostTracker
        from llm_code.runtime.model_aliases import resolve_model
        from llm_code.runtime.context import ProjectContext
        from llm_code.runtime.conversation import ConversationRuntime
        from llm_code.runtime.hooks import HookRunner
        from llm_code.runtime.permissions import PermissionMode, PermissionPolicy
        from llm_code.runtime.prompt import SystemPromptBuilder
        from llm_code.runtime.session import Session
        from llm_code.tools.registry import ToolRegistry

        api_key = os.environ.get(config.provider_api_key_env, "")
        base_url = config.provider_base_url or ""

        resolved_model = resolve_model(
            config.model, custom_aliases=config.model_aliases
        )
        state.cost_tracker = CostTracker(
            model=resolved_model,
            custom_pricing=config.pricing or None,
            max_budget_usd=config.max_budget_usd,
        )

        provider = ProviderClient.from_model(
            model=resolved_model,
            base_url=base_url,
            api_key=api_key,
            timeout=config.timeout,
            max_retries=config.max_retries,
            native_tools=config.native_tools,
        )

        # Core tools — collaborator-free set shared with run_quick_mode.
        # The register function is injected by the caller, or we fall
        # back to the shared runtime helper.
        state.tool_reg = ToolRegistry()
        if register_core_tools is None:
            from llm_code.runtime.core_tools import (
                register_core_tools as _rct,
            )
            register_core_tools = _rct
        register_core_tools(state.tool_reg, config)

        # AgentTool with lazy factory — the closure captures ``state``
        # so future mutation of ``state.runtime`` is visible by the
        # time a subagent actually spawns.
        try:
            from llm_code.runtime.subagent_factory import make_subagent_runtime
            from llm_code.tools.agent import AgentTool

            def _subagent_factory(model=None, role=None):
                parent_runtime = state.runtime
                if parent_runtime is None:
                    raise RuntimeError(
                        "AgentTool invoked before parent runtime initialized"
                    )
                return make_subagent_runtime(parent_runtime, role, model)

            if state.tool_reg.get("agent") is None:
                state.tool_reg.register(AgentTool(
                    runtime_factory=_subagent_factory,
                    max_depth=3,
                    current_depth=0,
                ))
        except (ImportError, ValueError):
            pass

        # Deferred tool manager + ToolSearchTool
        from llm_code.tools.deferred import DeferredToolManager
        from llm_code.tools.tool_search import ToolSearchTool
        state.deferred_tool_manager = DeferredToolManager()
        try:
            state.tool_reg.register(ToolSearchTool(state.deferred_tool_manager))
        except ValueError:
            pass

        context = ProjectContext.discover(state.cwd)
        session = Session.create(state.cwd)

        mode_map = {
            "read_only": PermissionMode.READ_ONLY,
            "workspace_write": PermissionMode.WORKSPACE_WRITE,
            "full_access": PermissionMode.FULL_ACCESS,
            "auto_accept": PermissionMode.AUTO_ACCEPT,
            "prompt": PermissionMode.PROMPT,
        }
        perm_mode = mode_map.get(config.permission_mode, PermissionMode.PROMPT)
        permissions = PermissionPolicy(
            mode=perm_mode,
            allow_tools=config.allowed_tools,
            deny_tools=config.denied_tools,
        )

        hooks = HookRunner(config.hooks)
        prompt_builder = SystemPromptBuilder()

        # Checkpoint manager (git-based undo)
        if (state.cwd / ".git").is_dir():
            try:
                from llm_code.runtime.checkpoint import CheckpointManager
                state.checkpoint_mgr = CheckpointManager(state.cwd)
            except Exception:
                pass

        # Recovery checkpoint (session state persistence)
        try:
            from llm_code.runtime.checkpoint_recovery import CheckpointRecovery
            state.recovery_checkpoint = CheckpointRecovery(
                Path.home() / ".llmcode" / "checkpoints"
            )
        except Exception:
            pass

        # Token budget
        if state.budget is not None:
            try:
                from llm_code.runtime.token_budget import TokenBudget
                state.token_budget = TokenBudget(target=state.budget)
            except Exception:
                pass

        # Skills — load from builtin / home / cwd / plugin dirs
        state.skills = _load_skills_for_cwd(state.cwd)

        # Memory (legacy key-value store)
        try:
            from llm_code.runtime.memory import MemoryStore
            memory_dir = Path.home() / ".llmcode" / "memory"
            state.memory = MemoryStore(memory_dir, state.cwd)
        except Exception:
            state.memory = None

        # Run daily memory distillation (today-*.md -> recent.md -> archive.md)
        try:
            from llm_code.runtime.memory_layers import distill_daily
            _mem_dir = Path.home() / ".llmcode" / "memory"
            if _mem_dir.is_dir():
                distill_daily(_mem_dir, _date.today())
        except Exception:
            pass  # non-critical -- skip silently

        # Typed memory (4-type taxonomy) with legacy migration
        try:
            from llm_code.runtime.memory_taxonomy import TypedMemoryStore
            project_hash = hashlib.sha256(str(state.cwd).encode()).hexdigest()[:8]
            typed_dir = Path.home() / ".llmcode" / "memory" / project_hash / "typed"
            state.typed_memory = TypedMemoryStore(typed_dir)
            if state.memory and not state.typed_memory.list_all():
                legacy_file = (
                    Path.home() / ".llmcode" / "memory" / project_hash / "memory.json"
                )
                if legacy_file.exists():
                    state.typed_memory.migrate_from_legacy(legacy_file)
        except Exception:
            pass

        # Register memory tools
        try:
            from llm_code.tools.memory_tools import (
                MemoryListTool,
                MemoryRecallTool,
                MemoryStoreTool,
            )
            if state.memory is not None:
                for tool_cls in (MemoryStoreTool, MemoryRecallTool, MemoryListTool):
                    try:
                        state.tool_reg.register(tool_cls(state.memory))
                    except ValueError:
                        pass
        except ImportError:
            pass

        # Register skill_load tool
        try:
            from llm_code.tools.skill_load import SkillLoadTool
            if state.skills is not None:
                state.tool_reg.register(SkillLoadTool(state.skills))
        except (ImportError, ValueError):
            pass

        # Register cron tools
        try:
            from llm_code.cron.storage import CronStorage
            from llm_code.tools.cron_tools import (
                CronCreateTool,
                CronDeleteTool,
                CronListTool,
            )
            state.cron_storage = CronStorage(
                state.cwd / ".llmcode" / "scheduled_tasks.json"
            )
            for tool in (
                CronCreateTool(state.cron_storage),
                CronListTool(state.cron_storage),
                CronDeleteTool(state.cron_storage),
            ):
                try:
                    state.tool_reg.register(tool)
                except ValueError:
                    pass
        except Exception:
            state.cron_storage = None

        # Register swarm tools (when enabled)
        try:
            if config.swarm.enabled:
                from llm_code.swarm.coordinator import Coordinator
                from llm_code.swarm.manager import SwarmManager
                from llm_code.tools.coordinator_tool import CoordinatorTool
                from llm_code.tools.swarm_tools import (
                    SwarmCreateTool,
                    SwarmDeleteTool,
                    SwarmListTool,
                    SwarmMessageTool,
                )

                swarm_mgr = SwarmManager(
                    swarm_dir=state.cwd / ".llmcode" / "swarm",
                    max_members=config.swarm.max_members,
                    backend_preference=config.swarm.backend,
                )
                state.swarm_manager = swarm_mgr
                for tool in (
                    SwarmCreateTool(swarm_mgr),
                    SwarmListTool(swarm_mgr),
                    SwarmMessageTool(swarm_mgr),
                    SwarmDeleteTool(swarm_mgr),
                ):
                    try:
                        state.tool_reg.register(tool)
                    except ValueError:
                        pass
                # Create and register coordinator tool. ``state.runtime``
                # is still None here — the coordinator grabs the provider
                # directly since that's what it needs during spawning.
                coordinator = Coordinator(
                    manager=swarm_mgr,
                    provider=(
                        state.runtime._provider if state.runtime else None
                    ),
                    config=config,
                )
                try:
                    state.tool_reg.register(CoordinatorTool(coordinator))
                except ValueError:
                    pass
        except Exception:
            state.swarm_manager = None

        # Register task lifecycle tools
        try:
            from llm_code.task.diagnostics import DiagnosticsEngine
            from llm_code.task.manager import TaskLifecycleManager
            from llm_code.task.verifier import Verifier
            from llm_code.tools.task_tools import (
                TaskCloseTool,
                TaskPlanTool,
                TaskVerifyTool,
            )

            task_dir = state.cwd / ".llmcode" / "tasks"
            diag_dir = state.cwd / ".llmcode" / "diagnostics"
            task_mgr = TaskLifecycleManager(task_dir=task_dir)
            verifier = Verifier(cwd=state.cwd)
            diagnostics = DiagnosticsEngine(diagnostics_dir=diag_dir)
            state.task_manager = task_mgr

            sid = session.id if session else ""

            for tool in (
                TaskPlanTool(task_mgr, session_id=sid),
                TaskVerifyTool(task_mgr, verifier, diagnostics),
                TaskCloseTool(task_mgr),
            ):
                try:
                    state.tool_reg.register(tool)
                except ValueError:
                    pass
        except Exception:
            state.task_manager = None

        # Register computer-use tools (only when enabled)
        if config.computer_use.enabled:
            try:
                from llm_code.tools.computer_use_tools import (
                    KeyboardTypeTool,
                    KeyPressTool,
                    MouseClickTool,
                    MouseDragTool,
                    ScreenshotTool,
                    ScrollTool,
                )
                cu_config = config.computer_use
                for tool in (
                    ScreenshotTool(cu_config),
                    MouseClickTool(cu_config),
                    KeyboardTypeTool(cu_config),
                    KeyPressTool(cu_config),
                    ScrollTool(cu_config),
                    MouseDragTool(cu_config),
                ):
                    try:
                        state.tool_reg.register(tool)
                    except ValueError:
                        pass
            except ImportError:
                pass

        # Register IDE tools if enabled
        if config.ide.enabled:
            try:
                from llm_code.ide.bridge import IDEBridge
                from llm_code.tools.ide_diagnostics import IDEDiagnosticsTool
                from llm_code.tools.ide_open import IDEOpenTool
                from llm_code.tools.ide_selection import IDESelectionTool

                state.ide_bridge = IDEBridge(config.ide)
                for tool in (
                    IDEOpenTool(state.ide_bridge),
                    IDEDiagnosticsTool(state.ide_bridge),
                    IDESelectionTool(state.ide_bridge),
                ):
                    try:
                        state.tool_reg.register(tool)
                    except ValueError:
                        pass
            except ImportError:
                state.ide_bridge = None

        # Register LSP tools if configured
        if config.lsp_servers or config.lsp_auto_detect:
            try:
                from llm_code.lsp.manager import LspServerManager
                from llm_code.lsp.tools import (
                    LspCallHierarchyTool,
                    LspDiagnosticsTool,
                    LspDocumentSymbolTool,
                    LspFindReferencesTool,
                    LspGotoDefinitionTool,
                    LspHoverTool,
                    LspImplementationTool,
                    LspWorkspaceSymbolTool,
                )
                state.lsp_manager = LspServerManager()
                for tool in (
                    LspGotoDefinitionTool(state.lsp_manager),
                    LspFindReferencesTool(state.lsp_manager),
                    LspDiagnosticsTool(state.lsp_manager),
                    LspHoverTool(state.lsp_manager),
                    LspDocumentSymbolTool(state.lsp_manager),
                    LspWorkspaceSymbolTool(state.lsp_manager),
                    LspImplementationTool(state.lsp_manager),
                    LspCallHierarchyTool(state.lsp_manager),
                ):
                    try:
                        state.tool_reg.register(tool)
                    except ValueError:
                        pass
            except ImportError:
                pass

        # Load user-defined agent roles from .llm-code/agents/*.md
        try:
            from llm_code.tools.agent_loader import load_all_agents
            state.user_agent_roles = load_all_agents(state.cwd)
        except Exception as exc:
            logger.warning("agent_loader: %r", exc)
            state.user_agent_roles = {}

        # Build project index
        try:
            from llm_code.runtime.indexer import ProjectIndexer
            state.project_index = ProjectIndexer(state.cwd).build_index()
        except Exception:
            pass

        # Initialize telemetry
        if getattr(config, "telemetry", None) and config.telemetry.enabled:
            try:
                from llm_code.runtime.telemetry import Telemetry
                state.telemetry = Telemetry(config.telemetry)
            except Exception:
                pass

        # Sandbox detection — info only
        try:
            from llm_code.runtime.sandbox import get_sandbox_info
            sandbox = get_sandbox_info()
            if sandbox["sandboxed"]:
                logger.info("Sandbox detected: %s", sandbox["type"])
        except Exception:
            pass

        # Runtime assembly — note: dialogs stays None here. The legacy
        # TUI adapter installs TextualDialogs on top, and v2.0.0's REPL
        # relies on runtime's HeadlessDialogs fallback.
        state.runtime = ConversationRuntime(
            provider=provider,
            tool_registry=state.tool_reg,
            permission_policy=permissions,
            hook_runner=hooks,
            prompt_builder=prompt_builder,
            config=config,
            session=session,
            context=context,
            checkpoint_manager=state.checkpoint_mgr,
            token_budget=state.token_budget,
            recovery_checkpoint=state.recovery_checkpoint,
            cost_tracker=state.cost_tracker,
            deferred_tool_manager=state.deferred_tool_manager,
            telemetry=state.telemetry,
            skills=state.skills,
            memory_store=state.memory,
            typed_memory_store=state.typed_memory,
            task_manager=state.task_manager,
            project_index=state.project_index,
            lsp_manager=state.lsp_manager,
            dialogs=state.dialogs,
        )

        # Register plan mode tools (need runtime reference)
        try:
            from llm_code.tools.plan_mode import EnterPlanModeTool, ExitPlanModeTool
            if state.tool_reg is not None:
                state.tool_reg.register(EnterPlanModeTool(runtime=state.runtime))
                state.tool_reg.register(ExitPlanModeTool(runtime=state.runtime))
        except Exception:
            pass

        return state


def _load_skills_for_cwd(cwd: Path) -> Any:
    """Port of ``LLMCodeTUI._reload_skills`` as a free function.

    Loads skills from the usual four layers:

    1. Built-in (llm_code.marketplace.builtin/*/skills)
    2. ``~/.llmcode/skills``
    3. ``<cwd>/.llmcode/skills``
    4. Enabled plugin skills (``~/.llmcode/plugins/*/(manifest.skills | skills/)``)

    Returns the loaded ``SkillSet`` or ``None`` on any failure.
    """
    try:
        import llm_code.marketplace as _mkt_pkg
        from llm_code.marketplace.installer import PluginInstaller
        from llm_code.runtime.skills import SkillLoader

        builtin_root = Path(_mkt_pkg.__file__).parent / "builtin"
        skill_dirs: list[Path] = []
        if builtin_root.is_dir():
            for plugin in sorted(builtin_root.iterdir()):
                sp = plugin / "skills"
                if sp.is_dir():
                    skill_dirs.append(sp)
        skill_dirs.extend([
            Path.home() / ".llmcode" / "skills",
            cwd / ".llmcode" / "skills",
        ])
        plugin_dir = Path.home() / ".llmcode" / "plugins"
        if plugin_dir.is_dir():
            pi = PluginInstaller(plugin_dir)
            for p in pi.list_installed():
                if p.enabled and p.manifest.skills:
                    sp = p.path / p.manifest.skills
                    if sp.is_dir():
                        skill_dirs.append(sp)
                direct = p.path / "skills"
                if p.enabled and direct.is_dir() and direct not in skill_dirs:
                    skill_dirs.append(direct)
        skills = SkillLoader().load_from_dirs(skill_dirs)
        logger.info(
            "skill load: dirs=%d auto=%d command=%d",
            len(skill_dirs),
            len(skills.auto_skills) if skills else 0,
            len(skills.command_skills) if skills else 0,
        )
        return skills
    except Exception as exc:
        logger.warning("skill load failed: %r", exc, exc_info=True)
        return None


__all__ = ["AppState"]
