# llm_code/tui/runtime_init.py
"""RuntimeInitializer -- extracted from app.py _init_runtime (~440 lines).

Builds all runtime subsystems: ProviderClient, CostTracker, ToolRegistry,
core tools, skills, memory, session, permission policy, MCP manager, LSP,
IDE bridge, swarm manager, task manager, VCR, and ConversationRuntime.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from llm_code.logging import get_logger

if TYPE_CHECKING:
    from llm_code.tui.app import LLMCodeTUI

logger = get_logger(__name__)


class RuntimeInitializer:
    """Handles full runtime initialization for the TUI.

    Stores a back-reference to the owning ``LLMCodeTUI`` app so it can
    access config, widgets, and other shared state.
    """

    def __init__(self, app: "LLMCodeTUI") -> None:
        self._app = app

    def initialize(self) -> None:  # noqa: C901 — large but linear setup
        """Initialize the conversation runtime (full body of former _init_runtime)."""
        if self._app._config is None:
            logger.warning("No config provided; runtime will not be initialized.")
            return

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

        api_key = os.environ.get(self._app._config.provider_api_key_env, "")
        base_url = self._app._config.provider_base_url or ""

        resolved_model = resolve_model(
            self._app._config.model, custom_aliases=self._app._config.model_aliases
        )
        self._app._cost_tracker = CostTracker(
            model=resolved_model,
            custom_pricing=self._app._config.pricing or None,
            max_budget_usd=self._app._config.max_budget_usd,
        )

        provider = ProviderClient.from_model(
            model=resolved_model,
            base_url=base_url,
            api_key=api_key,
            timeout=self._app._config.timeout,
            max_retries=self._app._config.max_retries,
            native_tools=self._app._config.native_tools,
        )

        # Register core tools -- collaborator-free set shared with the
        # headless ``run_quick_mode`` path so both exercise the same
        # tools. Instance-scoped tools (memory, skills, swarm, IDE, LSP,
        # etc.) are registered further down.
        from llm_code.tui.app import _register_core_tools

        self._app._tool_reg = ToolRegistry()
        _register_core_tools(self._app._tool_reg, self._app._config)

        # Register AgentTool with a lazy factory closure that captures
        # the app so the parent runtime -- built later in startup -- is reachable.
        try:
            from llm_code.runtime.subagent_factory import make_subagent_runtime
            from llm_code.tools.agent import AgentTool

            app_ref = self._app  # capture for closure

            def _subagent_factory(model=None, role=None):
                parent_runtime = getattr(app_ref, "_runtime", None)
                if parent_runtime is None:
                    raise RuntimeError(
                        "AgentTool invoked before parent runtime initialized"
                    )
                return make_subagent_runtime(parent_runtime, role, model)

            if self._app._tool_reg.get("agent") is None:
                self._app._tool_reg.register(AgentTool(
                    runtime_factory=_subagent_factory,
                    max_depth=3,
                    current_depth=0,
                ))
        except (ImportError, ValueError):
            pass

        # Deferred tool manager + ToolSearchTool
        from llm_code.tools.deferred import DeferredToolManager
        from llm_code.tools.tool_search import ToolSearchTool
        self._app._deferred_tool_manager = DeferredToolManager()
        try:
            self._app._tool_reg.register(ToolSearchTool(self._app._deferred_tool_manager))
        except ValueError:
            pass

        context = ProjectContext.discover(self._app._cwd)
        session = Session.create(self._app._cwd)

        mode_map = {
            "read_only": PermissionMode.READ_ONLY,
            "workspace_write": PermissionMode.WORKSPACE_WRITE,
            "full_access": PermissionMode.FULL_ACCESS,
            "auto_accept": PermissionMode.AUTO_ACCEPT,
            "prompt": PermissionMode.PROMPT,
        }
        perm_mode = mode_map.get(self._app._config.permission_mode, PermissionMode.PROMPT)
        permissions = PermissionPolicy(
            mode=perm_mode,
            allow_tools=self._app._config.allowed_tools,
            deny_tools=self._app._config.denied_tools,
        )

        hooks = HookRunner(self._app._config.hooks)
        prompt_builder = SystemPromptBuilder()

        # Checkpoint manager (git-based undo)
        checkpoint_mgr = None
        if (self._app._cwd / ".git").is_dir():
            try:
                from llm_code.runtime.checkpoint import CheckpointManager
                checkpoint_mgr = CheckpointManager(self._app._cwd)
            except Exception:
                pass
        self._app._checkpoint_mgr = checkpoint_mgr

        # Recovery checkpoint (session state persistence)
        recovery_checkpoint = None
        try:
            from llm_code.runtime.checkpoint_recovery import CheckpointRecovery
            recovery_checkpoint = CheckpointRecovery(Path.home() / ".llmcode" / "checkpoints")
        except Exception:
            pass

        # Token budget
        token_budget = None
        if self._app._budget is not None:
            try:
                from llm_code.runtime.token_budget import TokenBudget
                token_budget = TokenBudget(target=self._app._budget)
            except Exception:
                pass

        # Skills
        self._app._reload_skills()

        # Memory (legacy key-value store)
        try:
            from llm_code.runtime.memory import MemoryStore
            memory_dir = Path.home() / ".llmcode" / "memory"
            self._app._memory = MemoryStore(memory_dir, self._app._cwd)
        except Exception:
            self._app._memory = None

        # Run daily memory distillation (today-*.md -> recent.md -> archive.md)
        try:
            from llm_code.runtime.memory_layers import distill_daily
            from datetime import date as _date
            _mem_dir = Path.home() / ".llmcode" / "memory"
            if _mem_dir.is_dir():
                distill_daily(_mem_dir, _date.today())
        except Exception:
            pass  # non-critical -- skip silently

        # Typed memory (4-type taxonomy)
        self._app._typed_memory = None
        try:
            import hashlib
            from llm_code.runtime.memory_taxonomy import TypedMemoryStore
            project_hash = hashlib.sha256(str(self._app._cwd).encode()).hexdigest()[:8]
            typed_dir = Path.home() / ".llmcode" / "memory" / project_hash / "typed"
            self._app._typed_memory = TypedMemoryStore(typed_dir)
            # Auto-migrate legacy memory if typed store is empty
            if self._app._memory and not self._app._typed_memory.list_all():
                legacy_file = Path.home() / ".llmcode" / "memory" / project_hash / "memory.json"
                if legacy_file.exists():
                    self._app._typed_memory.migrate_from_legacy(legacy_file)
        except Exception:
            pass

        # Register memory tools
        try:
            from llm_code.tools.memory_tools import MemoryStoreTool, MemoryRecallTool, MemoryListTool
            if self._app._memory is not None:
                for tool_cls in (MemoryStoreTool, MemoryRecallTool, MemoryListTool):
                    try:
                        self._app._tool_reg.register(tool_cls(self._app._memory))
                    except ValueError:
                        pass
        except ImportError:
            pass

        # Register skill_load tool -- lets LLM actively load skills (complement to router)
        try:
            from llm_code.tools.skill_load import SkillLoadTool
            if self._app._skills is not None:
                self._app._tool_reg.register(SkillLoadTool(self._app._skills))
        except (ImportError, ValueError):
            pass

        # Register cron tools
        try:
            from llm_code.cron.storage import CronStorage
            from llm_code.tools.cron_tools import CronCreateTool, CronDeleteTool, CronListTool
            cron_storage = CronStorage(self._app._cwd / ".llmcode" / "scheduled_tasks.json")
            self._app._cron_storage = cron_storage
            for tool in (CronCreateTool(cron_storage), CronListTool(cron_storage), CronDeleteTool(cron_storage)):
                try:
                    self._app._tool_reg.register(tool)
                except ValueError:
                    pass
        except Exception:
            self._app._cron_storage = None

        # Register swarm tools
        self._app._swarm_manager = None
        try:
            if self._app._config.swarm.enabled:
                from llm_code.swarm.manager import SwarmManager
                from llm_code.tools.swarm_tools import (
                    SwarmCreateTool, SwarmDeleteTool,
                    SwarmListTool, SwarmMessageTool,
                )
                from llm_code.swarm.coordinator import Coordinator
                from llm_code.tools.coordinator_tool import CoordinatorTool

                swarm_mgr = SwarmManager(
                    swarm_dir=self._app._cwd / ".llmcode" / "swarm",
                    max_members=self._app._config.swarm.max_members,
                    backend_preference=self._app._config.swarm.backend,
                )
                self._app._swarm_manager = swarm_mgr
                for tool in (
                    SwarmCreateTool(swarm_mgr),
                    SwarmListTool(swarm_mgr),
                    SwarmMessageTool(swarm_mgr),
                    SwarmDeleteTool(swarm_mgr),
                ):
                    try:
                        self._app._tool_reg.register(tool)
                    except ValueError:
                        pass
                # Create and register coordinator tool
                coordinator = Coordinator(
                    manager=swarm_mgr,
                    provider=self._app._runtime._provider if self._app._runtime else None,
                    config=self._app._config,
                )
                try:
                    self._app._tool_reg.register(CoordinatorTool(coordinator))
                except ValueError:
                    pass
        except Exception:
            self._app._swarm_manager = None

        # Register task lifecycle tools
        self._app._task_manager = None
        try:
            from llm_code.task.manager import TaskLifecycleManager
            from llm_code.task.verifier import Verifier
            from llm_code.task.diagnostics import DiagnosticsEngine
            from llm_code.tools.task_tools import TaskCloseTool, TaskPlanTool, TaskVerifyTool

            task_dir = self._app._cwd / ".llmcode" / "tasks"
            diag_dir = self._app._cwd / ".llmcode" / "diagnostics"
            task_mgr = TaskLifecycleManager(task_dir=task_dir)
            verifier = Verifier(cwd=self._app._cwd)
            diagnostics = DiagnosticsEngine(diagnostics_dir=diag_dir)
            self._app._task_manager = task_mgr

            sid = session.id if session else ""

            for tool in (
                TaskPlanTool(task_mgr, session_id=sid),
                TaskVerifyTool(task_mgr, verifier, diagnostics),
                TaskCloseTool(task_mgr),
            ):
                try:
                    self._app._tool_reg.register(tool)
                except ValueError:
                    pass
        except Exception:
            self._app._task_manager = None

        # Register computer-use tools (only when enabled)
        if self._app._config.computer_use.enabled:
            try:
                from llm_code.tools.computer_use_tools import (
                    ScreenshotTool, MouseClickTool, KeyboardTypeTool,
                    KeyPressTool, ScrollTool, MouseDragTool,
                )
                cu_config = self._app._config.computer_use
                for tool in (
                    ScreenshotTool(cu_config), MouseClickTool(cu_config),
                    KeyboardTypeTool(cu_config), KeyPressTool(cu_config),
                    ScrollTool(cu_config), MouseDragTool(cu_config),
                ):
                    try:
                        self._app._tool_reg.register(tool)
                    except ValueError:
                        pass
            except ImportError:
                pass

        # Register IDE tools if enabled
        if self._app._config.ide.enabled:
            try:
                from llm_code.ide.bridge import IDEBridge
                from llm_code.tools.ide_open import IDEOpenTool
                from llm_code.tools.ide_diagnostics import IDEDiagnosticsTool
                from llm_code.tools.ide_selection import IDESelectionTool

                self._app._ide_bridge = IDEBridge(self._app._config.ide)
                for tool in (
                    IDEOpenTool(self._app._ide_bridge),
                    IDEDiagnosticsTool(self._app._ide_bridge),
                    IDESelectionTool(self._app._ide_bridge),
                ):
                    try:
                        self._app._tool_reg.register(tool)
                    except ValueError:
                        pass
            except ImportError:
                self._app._ide_bridge = None
        else:
            self._app._ide_bridge = None

        # Register LSP tools if configured
        self._app._lsp_manager = None
        if self._app._config.lsp_servers or self._app._config.lsp_auto_detect:
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
                self._app._lsp_manager = LspServerManager()
                for tool in (
                    LspGotoDefinitionTool(self._app._lsp_manager),
                    LspFindReferencesTool(self._app._lsp_manager),
                    LspDiagnosticsTool(self._app._lsp_manager),
                    LspHoverTool(self._app._lsp_manager),
                    LspDocumentSymbolTool(self._app._lsp_manager),
                    LspWorkspaceSymbolTool(self._app._lsp_manager),
                    LspImplementationTool(self._app._lsp_manager),
                    LspCallHierarchyTool(self._app._lsp_manager),
                ):
                    try:
                        self._app._tool_reg.register(tool)
                    except ValueError:
                        pass
            except ImportError:
                pass

        # Load user-defined agent roles from .llm-code/agents/*.md
        try:
            from llm_code.tools.agent_loader import load_all_agents
            self._app._user_agent_roles = load_all_agents(self._app._cwd)
        except Exception as exc:
            logger.warning("agent_loader: %r", exc)
            self._app._user_agent_roles = {}

        # Build project index
        self._app._project_index = None
        try:
            from llm_code.runtime.indexer import ProjectIndexer
            self._app._project_index = ProjectIndexer(self._app._cwd).build_index()
        except Exception:
            pass

        # Initialize telemetry -- pass the config straight through; both
        # `from llm_code.runtime.config import TelemetryConfig` and
        # `from llm_code.runtime.telemetry import TelemetryConfig` resolve
        # to the same class (see telemetry config consolidation, Plan 5.5).
        telemetry = None
        if getattr(self._app._config, "telemetry", None) and self._app._config.telemetry.enabled:
            try:
                from llm_code.runtime.telemetry import Telemetry
                telemetry = Telemetry(self._app._config.telemetry)
            except Exception:
                pass

        # Sandbox detection -- inject info into context
        try:
            from llm_code.runtime.sandbox import get_sandbox_info
            sandbox = get_sandbox_info()
            if sandbox["sandboxed"]:
                logger.info("Sandbox detected: %s", sandbox["type"])
        except Exception:
            pass

        # Create TextualDialogs for modal screen prompts
        from llm_code.tui.dialogs.textual_backend import TextualDialogs
        self._app._dialogs = TextualDialogs(self._app)

        # Create runtime with all subsystem references
        self._app._runtime = ConversationRuntime(
            provider=provider,
            tool_registry=self._app._tool_reg,
            permission_policy=permissions,
            hook_runner=hooks,
            prompt_builder=prompt_builder,
            config=self._app._config,
            session=session,
            context=context,
            checkpoint_manager=checkpoint_mgr,
            token_budget=token_budget,
            recovery_checkpoint=recovery_checkpoint,
            cost_tracker=self._app._cost_tracker,
            deferred_tool_manager=self._app._deferred_tool_manager,
            telemetry=telemetry,
            skills=self._app._skills,
            memory_store=self._app._memory,
            typed_memory_store=self._app._typed_memory,
            task_manager=self._app._task_manager,
            project_index=self._app._project_index,
            lsp_manager=self._app._lsp_manager,
            dialogs=self._app._dialogs,
        )
        # Register plan mode tools (need runtime reference)
        try:
            from llm_code.tools.plan_mode import EnterPlanModeTool, ExitPlanModeTool
            if self._app._tool_reg:
                self._app._tool_reg.register(EnterPlanModeTool(runtime=self._app._runtime))
                self._app._tool_reg.register(ExitPlanModeTool(runtime=self._app._runtime))
        except Exception:
            pass

        # Install MCP event sink so non-root server spawns surface an
        # inline approval widget.
        try:
            self._app._runtime.set_mcp_event_sink(self._app._on_mcp_approval_event)
        except Exception:
            pass
