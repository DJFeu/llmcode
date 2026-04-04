# llm_code/tui/app.py
"""LLMCodeTUI — Textual fullscreen app composing all widgets."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult

from llm_code.tui.chat_view import ChatScrollView, UserMessage, AssistantText
from llm_code.tui.header_bar import HeaderBar
from llm_code.tui.input_bar import InputBar
from llm_code.tui.status_bar import StatusBar
from llm_code.tui.theme import APP_CSS
from llm_code.logging import get_logger

logger = get_logger(__name__)


class LLMCodeTUI(App):
    """Fullscreen TUI matching Claude Code's visual experience."""

    TITLE = "llm-code"
    CSS = APP_CSS
    ENABLE_MOUSE_SUPPORT = False  # CRITICAL: allow terminal mouse selection + copy

    def __init__(
        self,
        config: Any = None,
        cwd: Path | None = None,
        budget: int | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._cwd = cwd or Path.cwd()
        self._budget = budget
        self._runtime = None
        self._cost_tracker = None
        self._input_tokens = 0
        self._output_tokens = 0
        self._tool_reg = None
        self._deferred_tool_manager = None
        self._checkpoint_mgr = None
        self._mcp_manager = None
        self._skills = None
        self._memory = None
        self._cron_storage = None
        self._swarm_manager = None
        self._task_manager = None
        self._ide_bridge = None
        self._lsp_manager = None
        self._project_index = None
        self._coordinator_class = None
        self._coordinator_tool_class = None

    def compose(self) -> ComposeResult:
        yield HeaderBar(id="header-bar")
        yield ChatScrollView()
        yield InputBar()
        yield StatusBar(id="status-bar")

    def on_mount(self) -> None:
        self._init_runtime()
        header = self.query_one(HeaderBar)
        if self._config:
            header.model = getattr(self._config, "model", "")
        header.project = self._cwd.name
        header.branch = self._detect_branch()
        # Start MCP servers async
        self.run_worker(self._init_mcp(), name="init_mcp")

    def _detect_branch(self) -> str:
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=self._cwd, capture_output=True, text=True, timeout=3,
            )
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""

    def _init_runtime(self) -> None:
        """Initialize the conversation runtime — mirrors LLMCodeCLI._init_session."""
        if self._config is None:
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
        from llm_code.tools.bash import BashTool
        from llm_code.tools.edit_file import EditFileTool
        from llm_code.tools.git_tools import (
            GitBranchTool, GitCommitTool, GitDiffTool,
            GitLogTool, GitPushTool, GitStashTool, GitStatusTool,
        )
        from llm_code.tools.glob_search import GlobSearchTool
        from llm_code.tools.grep_search import GrepSearchTool
        from llm_code.tools.notebook_edit import NotebookEditTool
        from llm_code.tools.notebook_read import NotebookReadTool
        from llm_code.tools.read_file import ReadFileTool
        from llm_code.tools.registry import ToolRegistry
        from llm_code.tools.write_file import WriteFileTool

        api_key = os.environ.get(self._config.provider_api_key_env, "")
        base_url = self._config.provider_base_url or ""

        resolved_model = resolve_model(
            self._config.model, custom_aliases=self._config.model_aliases
        )
        self._cost_tracker = CostTracker(
            model=resolved_model,
            custom_pricing=self._config.pricing or None,
            max_budget_usd=self._config.max_budget_usd,
        )

        provider = ProviderClient.from_model(
            model=resolved_model,
            base_url=base_url,
            api_key=api_key,
            timeout=self._config.timeout,
            max_retries=self._config.max_retries,
            native_tools=self._config.native_tools,
        )

        # Register core tools — local models get longer bash timeout
        _base_url = self._config.provider_base_url or ""
        _is_local = any(h in _base_url for h in ("localhost", "127.0.0.1", "0.0.0.0", "192.168.", "10.", "172."))
        _bash_timeout = 0 if _is_local else 30  # 0 = no timeout for local models

        self._tool_reg = ToolRegistry()
        for tool in (
            ReadFileTool(),
            WriteFileTool(),
            EditFileTool(),
            BashTool(default_timeout=_bash_timeout),
            GlobSearchTool(),
            GrepSearchTool(),
            NotebookReadTool(),
            NotebookEditTool(),
        ):
            try:
                self._tool_reg.register(tool)
            except ValueError:
                pass

        for cls in (
            GitStatusTool, GitDiffTool, GitLogTool, GitCommitTool,
            GitPushTool, GitStashTool, GitBranchTool,
        ):
            try:
                self._tool_reg.register(cls())
            except ValueError:
                pass

        # Try to register AgentTool
        try:
            from llm_code.tools.agent import AgentTool
            if self._tool_reg.get("agent") is None:
                self._tool_reg.register(AgentTool(
                    runtime_factory=None, max_depth=3, current_depth=0,
                ))
        except (ImportError, ValueError):
            pass

        # Deferred tool manager + ToolSearchTool
        from llm_code.tools.deferred import DeferredToolManager
        from llm_code.tools.tool_search import ToolSearchTool
        self._deferred_tool_manager = DeferredToolManager()
        try:
            self._tool_reg.register(ToolSearchTool(self._deferred_tool_manager))
        except ValueError:
            pass

        context = ProjectContext.discover(self._cwd)
        session = Session.create(self._cwd)

        mode_map = {
            "read_only": PermissionMode.READ_ONLY,
            "workspace_write": PermissionMode.WORKSPACE_WRITE,
            "full_access": PermissionMode.FULL_ACCESS,
            "auto_accept": PermissionMode.AUTO_ACCEPT,
            "prompt": PermissionMode.PROMPT,
        }
        perm_mode = mode_map.get(self._config.permission_mode, PermissionMode.PROMPT)
        permissions = PermissionPolicy(
            mode=perm_mode,
            allow_tools=self._config.allowed_tools,
            deny_tools=self._config.denied_tools,
        )

        hooks = HookRunner(self._config.hooks)
        prompt_builder = SystemPromptBuilder()

        # Checkpoint manager (git-based undo)
        checkpoint_mgr = None
        if (self._cwd / ".git").is_dir():
            try:
                from llm_code.runtime.checkpoint import CheckpointManager
                checkpoint_mgr = CheckpointManager(self._cwd)
            except Exception:
                pass
        self._checkpoint_mgr = checkpoint_mgr

        # Recovery checkpoint (session state persistence)
        recovery_checkpoint = None
        try:
            from llm_code.runtime.checkpoint_recovery import CheckpointRecovery
            recovery_checkpoint = CheckpointRecovery(Path.home() / ".llm-code" / "checkpoints")
        except Exception:
            pass

        # Token budget
        token_budget = None
        if self._budget is not None:
            try:
                from llm_code.runtime.token_budget import TokenBudget
                token_budget = TokenBudget(target=self._budget)
            except Exception:
                pass

        # Skills
        try:
            from llm_code.runtime.skills import SkillLoader
            from llm_code.marketplace.installer import PluginInstaller
            skill_dirs: list[Path] = [
                Path.home() / ".llm-code" / "skills",
                self._cwd / ".llm-code" / "skills",
            ]
            plugin_dir = Path.home() / ".llm-code" / "plugins"
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
            self._skills = SkillLoader().load_from_dirs(skill_dirs)
        except Exception:
            self._skills = None

        # Memory
        try:
            from llm_code.runtime.memory import MemoryStore
            memory_dir = Path.home() / ".llm-code" / "memory"
            self._memory = MemoryStore(memory_dir, self._cwd)
        except Exception:
            self._memory = None

        # Register memory tools
        try:
            from llm_code.tools.memory_tools import MemoryStoreTool, MemoryRecallTool, MemoryListTool
            if self._memory is not None:
                for tool_cls in (MemoryStoreTool, MemoryRecallTool, MemoryListTool):
                    try:
                        self._tool_reg.register(tool_cls(self._memory))
                    except ValueError:
                        pass
        except ImportError:
            pass

        # Register cron tools
        try:
            from llm_code.cron.storage import CronStorage
            from llm_code.tools.cron_create import CronCreateTool
            from llm_code.tools.cron_list import CronListTool
            from llm_code.tools.cron_delete import CronDeleteTool
            cron_storage = CronStorage(self._cwd / ".llm-code" / "scheduled_tasks.json")
            self._cron_storage = cron_storage
            for tool in (CronCreateTool(cron_storage), CronListTool(cron_storage), CronDeleteTool(cron_storage)):
                try:
                    self._tool_reg.register(tool)
                except ValueError:
                    pass
        except Exception:
            self._cron_storage = None

        # Register swarm tools
        self._swarm_manager = None
        try:
            if self._config.swarm.enabled:
                from llm_code.swarm.manager import SwarmManager
                from llm_code.tools.swarm_create import SwarmCreateTool
                from llm_code.tools.swarm_list import SwarmListTool
                from llm_code.tools.swarm_message import SwarmMessageTool
                from llm_code.tools.swarm_delete import SwarmDeleteTool
                from llm_code.swarm.coordinator import Coordinator
                from llm_code.tools.coordinator_tool import CoordinatorTool

                swarm_mgr = SwarmManager(
                    swarm_dir=self._cwd / ".llm-code" / "swarm",
                    max_members=self._config.swarm.max_members,
                    backend_preference=self._config.swarm.backend,
                )
                self._swarm_manager = swarm_mgr
                for tool in (
                    SwarmCreateTool(swarm_mgr),
                    SwarmListTool(swarm_mgr),
                    SwarmMessageTool(swarm_mgr),
                    SwarmDeleteTool(swarm_mgr),
                ):
                    try:
                        self._tool_reg.register(tool)
                    except ValueError:
                        pass
                self._coordinator_class = Coordinator
                self._coordinator_tool_class = CoordinatorTool
        except Exception:
            self._swarm_manager = None

        # Register task lifecycle tools
        self._task_manager = None
        try:
            from llm_code.task.manager import TaskLifecycleManager
            from llm_code.task.verifier import Verifier
            from llm_code.task.diagnostics import DiagnosticsEngine
            from llm_code.tools.task_plan import TaskPlanTool
            from llm_code.tools.task_verify import TaskVerifyTool
            from llm_code.tools.task_close import TaskCloseTool

            task_dir = self._cwd / ".llm-code" / "tasks"
            diag_dir = self._cwd / ".llm-code" / "diagnostics"
            task_mgr = TaskLifecycleManager(task_dir=task_dir)
            verifier = Verifier(cwd=self._cwd)
            diagnostics = DiagnosticsEngine(diagnostics_dir=diag_dir)
            self._task_manager = task_mgr

            sid = session.id if session else ""

            for tool in (
                TaskPlanTool(task_mgr, session_id=sid),
                TaskVerifyTool(task_mgr, verifier, diagnostics),
                TaskCloseTool(task_mgr),
            ):
                try:
                    self._tool_reg.register(tool)
                except ValueError:
                    pass
        except Exception:
            self._task_manager = None

        # Register computer-use tools (only when enabled)
        if self._config.computer_use.enabled:
            try:
                from llm_code.tools.computer_use_tools import (
                    ScreenshotTool, MouseClickTool, KeyboardTypeTool,
                    KeyPressTool, ScrollTool, MouseDragTool,
                )
                cu_config = self._config.computer_use
                for tool in (
                    ScreenshotTool(cu_config), MouseClickTool(cu_config),
                    KeyboardTypeTool(cu_config), KeyPressTool(cu_config),
                    ScrollTool(cu_config), MouseDragTool(cu_config),
                ):
                    try:
                        self._tool_reg.register(tool)
                    except ValueError:
                        pass
            except ImportError:
                pass

        # Register IDE tools if enabled
        if self._config.ide.enabled:
            try:
                from llm_code.ide.bridge import IDEBridge
                from llm_code.tools.ide_open import IDEOpenTool
                from llm_code.tools.ide_diagnostics import IDEDiagnosticsTool
                from llm_code.tools.ide_selection import IDESelectionTool

                self._ide_bridge = IDEBridge(self._config.ide)
                for tool in (
                    IDEOpenTool(self._ide_bridge),
                    IDEDiagnosticsTool(self._ide_bridge),
                    IDESelectionTool(self._ide_bridge),
                ):
                    try:
                        self._tool_reg.register(tool)
                    except ValueError:
                        pass
            except ImportError:
                self._ide_bridge = None
        else:
            self._ide_bridge = None

        # Register LSP tools if configured
        self._lsp_manager = None
        if self._config.lsp_servers or self._config.lsp_auto_detect:
            try:
                from llm_code.lsp.manager import LspServerManager
                from llm_code.lsp.tools import LspGotoDefinitionTool, LspFindReferencesTool, LspDiagnosticsTool
                self._lsp_manager = LspServerManager()
                for tool in (
                    LspGotoDefinitionTool(self._lsp_manager),
                    LspFindReferencesTool(self._lsp_manager),
                    LspDiagnosticsTool(self._lsp_manager),
                ):
                    try:
                        self._tool_reg.register(tool)
                    except ValueError:
                        pass
            except ImportError:
                pass

        # Build project index
        self._project_index = None
        try:
            from llm_code.runtime.indexer import ProjectIndexer
            self._project_index = ProjectIndexer(self._cwd).build_index()
        except Exception:
            pass

        # Initialize telemetry
        telemetry = None
        if getattr(self._config, "telemetry", None) and self._config.telemetry.enabled:
            try:
                from llm_code.runtime.telemetry import Telemetry, TelemetryConfig
                telemetry = Telemetry(TelemetryConfig(
                    enabled=True,
                    endpoint=self._config.telemetry.endpoint,
                    service_name=self._config.telemetry.service_name,
                ))
            except Exception:
                pass

        # Sandbox detection — inject info into context
        try:
            from llm_code.runtime.sandbox import get_sandbox_info
            sandbox = get_sandbox_info()
            if sandbox["sandboxed"]:
                logger.info("Sandbox detected: %s", sandbox["type"])
        except Exception:
            pass

        # Create runtime with all subsystem references
        self._runtime = ConversationRuntime(
            provider=provider,
            tool_registry=self._tool_reg,
            permission_policy=permissions,
            hook_runner=hooks,
            prompt_builder=prompt_builder,
            config=self._config,
            session=session,
            context=context,
            checkpoint_manager=checkpoint_mgr,
            token_budget=token_budget,
            recovery_checkpoint=recovery_checkpoint,
            cost_tracker=self._cost_tracker,
            deferred_tool_manager=self._deferred_tool_manager,
            telemetry=telemetry,
            skills=self._skills,
            memory_store=self._memory,
            task_manager=self._task_manager,
            project_index=self._project_index,
        )

    async def _init_mcp(self) -> None:
        """Start MCP servers and register their tools (async, called after _init_runtime)."""
        if self._config is None or not self._config.mcp_servers:
            self._mcp_manager = None
            return
        try:
            from llm_code.mcp.manager import McpServerManager
            from llm_code.mcp.types import McpServerConfig

            manager = McpServerManager()
            configs: dict[str, McpServerConfig] = {}
            for name, raw in self._config.mcp_servers.items():
                if isinstance(raw, dict):
                    configs[name] = McpServerConfig(
                        command=raw.get("command"),
                        args=tuple(raw.get("args", ())),
                        env=raw.get("env"),
                        transport_type=raw.get("transport_type", "stdio"),
                        url=raw.get("url"),
                        headers=raw.get("headers"),
                    )
            await manager.start_all(configs)
            registered = await manager.register_all_tools(self._tool_reg)
            self._mcp_manager = manager
            if self._runtime is not None:
                self._runtime._mcp_manager = manager
            if registered:
                logger.info("MCP: %d server(s), %d tool(s) registered", len(configs), registered)
        except Exception as exc:
            logger.warning("MCP initialization failed: %s", exc)
            self._mcp_manager = None

    def on_input_bar_submitted(self, event: InputBar.Submitted) -> None:
        """Handle user input submission."""
        text = event.value.strip()
        if not text:
            return

        chat = self.query_one(ChatScrollView)
        chat.add_entry(UserMessage(text))

        if text.startswith("/"):
            self._handle_slash_command(text)
        else:
            self.run_worker(self._run_turn(text), name="run_turn")

    def on_input_bar_cancelled(self, event: InputBar.Cancelled) -> None:
        """Handle Escape — cancel running generation."""
        pass  # Phase 2: cancel runtime

    async def _run_turn(self, user_input: str) -> None:
        """Run a conversation turn with full streaming event handling."""
        import asyncio
        import time
        from llm_code.api.types import (
            StreamTextDelta, StreamThinkingDelta, StreamToolExecStart,
            StreamToolExecResult, StreamToolProgress, StreamMessageStop,
        )
        from llm_code.tui.chat_widgets import SpinnerLine, ThinkingBlock, ToolBlock, TurnSummary

        if self._runtime is None:
            chat = self.query_one(ChatScrollView)
            chat.add_entry(AssistantText("Error: runtime not initialized. Check configuration."))
            return

        chat = self.query_one(ChatScrollView)
        input_bar = self.query_one(InputBar)
        status = self.query_one(StatusBar)

        input_bar.disabled = True
        status.is_streaming = True

        # Reset per-turn counters
        turn_input_tokens = 0
        turn_output_tokens = 0

        spinner = SpinnerLine()
        spinner.phase = "waiting"
        chat.add_entry(spinner)
        start = time.monotonic()

        async def update_spinner():
            while input_bar.disabled:
                await asyncio.sleep(0.1)
                spinner.elapsed = time.monotonic() - start
                spinner.advance_frame()

        timer_task = asyncio.create_task(update_spinner())

        assistant = AssistantText()
        assistant_added = False
        thinking_buffer = ""
        thinking_start = time.monotonic()

        async def remove_spinner() -> None:
            """Remove spinner if it is currently mounted."""
            if spinner.is_mounted:
                await spinner.remove()

        try:
            async for event in self._runtime.run_turn(user_input):
                if isinstance(event, StreamTextDelta):
                    if not assistant_added:
                        await remove_spinner()
                        chat.add_entry(assistant)
                        assistant_added = True
                    assistant.append_text(event.text)
                    chat.resume_auto_scroll()

                elif isinstance(event, StreamThinkingDelta):
                    spinner.phase = "thinking"
                    thinking_buffer += event.text

                elif isinstance(event, StreamToolExecStart):
                    await remove_spinner()
                    tool_widget = ToolBlock.create(
                        event.tool_name, event.args_summary, "", is_error=False,
                    )
                    chat.add_entry(tool_widget)
                    spinner.phase = "running"
                    spinner._tool_name = event.tool_name
                    chat.add_entry(spinner)

                elif isinstance(event, StreamToolExecResult):
                    await remove_spinner()
                    tool_widget = ToolBlock.create(
                        event.tool_name, "", event.output[:200], event.is_error,
                    )
                    chat.add_entry(tool_widget)
                    spinner.phase = "processing"
                    thinking_start = time.monotonic()
                    chat.add_entry(spinner)

                elif isinstance(event, StreamToolProgress):
                    spinner.phase = "running"
                    spinner._tool_name = event.tool_name

                elif isinstance(event, StreamMessageStop):
                    if event.usage:
                        turn_input_tokens += event.usage.input_tokens
                        turn_output_tokens += event.usage.output_tokens
                        self._input_tokens += event.usage.input_tokens
                        self._output_tokens += event.usage.output_tokens
                        if self._cost_tracker:
                            self._cost_tracker.add_usage(
                                event.usage.input_tokens, event.usage.output_tokens,
                            )

        except Exception as exc:
            chat.add_entry(AssistantText(f"Error: {exc}"))
        finally:
            timer_task.cancel()
            try:
                await remove_spinner()
            except Exception:
                pass
            input_bar.disabled = False
            status.is_streaming = False

        if thinking_buffer:
            elapsed = time.monotonic() - thinking_start
            tokens = len(thinking_buffer) // 4
            chat.add_entry(ThinkingBlock(thinking_buffer, elapsed, tokens))

        elapsed = time.monotonic() - start
        cost = self._cost_tracker.format_cost() if self._cost_tracker else ""
        chat.add_entry(TurnSummary.create(elapsed, turn_input_tokens, turn_output_tokens, cost))

        status.tokens = self._output_tokens  # session total in status bar
        status.cost = cost
        chat.resume_auto_scroll()

    def _handle_slash_command(self, text: str) -> None:
        """Handle slash commands."""
        chat = self.query_one(ChatScrollView)
        if text.strip() in ("/exit", "/quit"):
            self.exit()
        elif text.strip() == "/help":
            chat.add_entry(AssistantText("Available: /help /exit /quit /model /clear"))
        elif text.strip() == "/clear":
            chat.remove_children()
        else:
            chat.add_entry(AssistantText(f"Unknown command: {text}"))
