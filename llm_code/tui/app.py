# llm_code/tui/app.py
"""LLMCodeTUI — Textual fullscreen app composing all widgets."""
from __future__ import annotations

import os
import re
import shutil
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
        self._permission_pending = False
        self._pending_images: list = []
        self._voice_active = False
        self._vcr_recorder = None

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
        self._render_welcome()
        # Focus input bar so it receives key events
        self.query_one(InputBar).focus()
        # Start MCP servers async
        self.run_worker(self._init_mcp(), name="init_mcp")

    def _render_welcome(self) -> None:
        """Show styled welcome banner in chat area."""
        import sys
        from textual.widgets import Static
        from rich.text import Text as RichText

        chat = self.query_one(ChatScrollView)

        logo_lines = [
            "  ██╗     ██╗     ███╗   ███╗",
            "  ██║     ██║     ████╗ ████║",
            "  ██║     ██║     ██╔████╔██║",
            "  ██║     ██║     ██║╚██╔╝██║",
            "  ███████╗███████╗██║ ╚═╝ ██║",
            "  ╚══════╝╚══════╝╚═╝     ╚═╝",
            "   ██████╗ ██████╗ ██████╗ ███████╗",
            "  ██╔════╝██╔═══██╗██╔══██╗██╔════╝",
            "  ██║     ██║   ██║██║  ██║█████╗",
            "  ██║     ██║   ██║██║  ██║██╔══╝",
            "  ╚██████╗╚██████╔╝██████╔╝███████╗",
            "   ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝",
        ]

        model = self._config.model if self._config else "(not set)"
        branch = self._detect_branch()
        workspace = self._cwd.name
        if branch:
            workspace += f" · {branch}"
        perm = self._config.permission_mode if self._config else "prompt"
        paste_key = "Cmd+V" if sys.platform == "darwin" else "Ctrl+V"

        text = RichText()
        for line in logo_lines:
            text.append(line + "\n", style="bold cyan")
        text.append("\n")
        for label, value in [
            ("Model", model),
            ("Workspace", workspace),
            ("Directory", str(self._cwd)),
            ("Permissions", perm),
        ]:
            text.append(f"  {label:<14}", style="yellow")
            text.append(f" {value}\n", style="bold white")
        text.append("\n")
        for label, value in [
            ("Quick start", "/help · /skill · /mcp"),
            ("Multiline", "Shift+Enter"),
            ("Images", f"{paste_key} pastes"),
        ]:
            text.append(f"  {label:<14}", style="dim")
            text.append(f" {value}\n", style="white")
        text.append("\n")
        text.append("  Ready\n", style="bold green")

        banner = Static(text)
        banner.styles.height = "auto"
        chat.add_entry(banner)

    @staticmethod
    def _is_safe_name(name: str) -> bool:
        """Validate skill/plugin name — alphanumeric, hyphens, underscores, dots only."""
        return bool(re.match(r'^[a-zA-Z0-9_.-]+$', name))

    @staticmethod
    def _is_valid_repo(source: str) -> bool:
        """Validate GitHub repo format: owner/name with safe characters."""
        cleaned = source.replace("https://github.com/", "").rstrip("/")
        parts = cleaned.split("/")
        if len(parts) != 2:
            return False
        return all(re.match(r'^[a-zA-Z0-9_.-]+$', p) for p in parts)

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

    def _hot_start_mcp(self, name: str, raw_config: dict) -> None:
        """Start a single MCP server without restart."""
        async def _start():
            try:
                from llm_code.mcp.manager import McpServerManager
                from llm_code.mcp.types import McpServerConfig

                cfg = McpServerConfig(
                    command=raw_config.get("command"),
                    args=tuple(raw_config.get("args", ())),
                    env=raw_config.get("env"),
                    transport_type=raw_config.get("transport_type", "stdio"),
                    url=raw_config.get("url"),
                    headers=raw_config.get("headers"),
                )
                if self._mcp_manager is None:
                    self._mcp_manager = McpServerManager()
                await self._mcp_manager.start_all({name: cfg})
                registered = await self._mcp_manager.register_all_tools(self._tool_reg)
                if self._runtime is not None:
                    self._runtime._mcp_manager = self._mcp_manager
                chat = self.query_one(ChatScrollView)
                chat.add_entry(AssistantText(
                    f"MCP server '{name}' started ({registered} tools registered)."
                ))
            except Exception as exc:
                chat = self.query_one(ChatScrollView)
                chat.add_entry(AssistantText(f"MCP start failed: {exc}"))

        self.run_worker(_start(), name=f"mcp_start_{name}")

    def on_input_bar_submitted(self, event: InputBar.Submitted) -> None:
        """Handle user input submission."""
        text = event.value.strip()
        if not text:
            return

        chat = self.query_one(ChatScrollView)
        chat.resume_auto_scroll()  # Resume on new input
        chat.add_entry(UserMessage(text))

        if text.startswith("/"):
            self._handle_slash_command(text)
        else:
            self.run_worker(self._run_turn(text), name="run_turn")

    def on_input_bar_cancelled(self, event: InputBar.Cancelled) -> None:
        """Handle Escape — cancel running generation."""
        pass  # Phase 2: cancel runtime

    def on_key(self, event: "events.Key") -> None:
        """Handle single-key permission responses (y/n/a), image paste, and scroll."""
        # Ctrl+D — quit
        if event.key == "ctrl+d":
            self.exit()
            return

        # Ctrl+V — paste image from clipboard
        if event.key == "ctrl+v":
            try:
                from llm_code.cli.image import capture_clipboard_image
                img = capture_clipboard_image()
                if img is not None:
                    self._pending_images.append(img)
                    chat = self.query_one(ChatScrollView)
                    chat.add_entry(AssistantText("Image attached from clipboard"))
                    event.prevent_default()
                    event.stop()
                    return
            except (ImportError, FileNotFoundError, OSError):
                pass  # No clipboard tool available
            except Exception as exc:
                logger.warning("Clipboard paste error: %s", exc)

        # Page Up / Page Down for chat scrolling
        if event.key == "pageup":
            chat = self.query_one(ChatScrollView)
            chat.scroll_up(animate=False)
            chat.pause_auto_scroll()
            event.prevent_default()
            return
        if event.key == "pagedown":
            chat = self.query_one(ChatScrollView)
            chat.scroll_down(animate=False)
            chat.resume_auto_scroll()
            event.prevent_default()
            return

        # Permission handling (y/n/a)
        if not self._permission_pending or self._runtime is None:
            return
        response_map = {"y": "allow", "n": "deny", "a": "always"}
        response = response_map.get(event.key)
        if response is not None:
            self._runtime.send_permission_response(response)
            event.prevent_default()
            event.stop()

    async def _run_turn(self, user_input: str) -> None:
        """Run a conversation turn with full streaming event handling."""
        import asyncio
        import time
        from llm_code.api.types import (
            StreamPermissionRequest, StreamTextDelta, StreamThinkingDelta,
            StreamToolExecStart, StreamToolExecResult, StreamToolProgress,
            StreamMessageStop,
        )
        from llm_code.tui.chat_widgets import (
            PermissionInline, SpinnerLine, ThinkingBlock, ToolBlock, TurnSummary,
        )

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

        perm_widget = None

        try:
            async for event in self._runtime.run_turn(user_input):
                # Clean up permission widget from previous iteration
                if self._permission_pending and not isinstance(event, StreamPermissionRequest):
                    self._permission_pending = False
                    if perm_widget is not None and perm_widget.is_mounted:
                        await perm_widget.remove()
                        perm_widget = None
                    # Re-add spinner while tool executes
                    spinner.phase = "running"
                    chat.add_entry(spinner)

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

                elif isinstance(event, StreamPermissionRequest):
                    await remove_spinner()
                    perm_widget = PermissionInline(
                        event.tool_name, event.args_preview,
                    )
                    chat.add_entry(perm_widget)
                    self._permission_pending = True
                    # No explicit wait — the runtime generator is suspended
                    # on its own asyncio.Future. The async for loop blocks on
                    # __anext__ until y/n/a resolves the Future via on_key →
                    # send_permission_response. Cleanup at top of loop.

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
            self._permission_pending = False
            try:
                await remove_spinner()
            except Exception:
                pass
            if perm_widget is not None and perm_widget.is_mounted:
                try:
                    await perm_widget.remove()
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
        """Handle slash commands — dispatches to _cmd_* methods."""
        from llm_code.cli.commands import parse_slash_command

        cmd = parse_slash_command(text)
        if cmd is None:
            return

        name = cmd.name
        args = cmd.args.strip()

        handler = getattr(self, f"_cmd_{name}", None)
        if handler is not None:
            handler(args)
        else:
            chat = self.query_one(ChatScrollView)
            chat.add_entry(AssistantText(f"Unknown command: /{name} — type /help for help"))

    def _cmd_exit(self, args: str) -> None:
        self.exit()

    _cmd_quit = _cmd_exit

    def _cmd_help(self, args: str) -> None:
        chat = self.query_one(ChatScrollView)
        lines = ["Available commands:"]
        for cmd_name, desc in [
            ("/help", "Show this help"),
            ("/clear", "Clear conversation"),
            ("/model <name>", "Switch model"),
            ("/cost", "Token usage"),
            ("/budget <n>", "Set token budget"),
            ("/undo [list]", "Undo last file change"),
            ("/cd <dir>", "Change directory"),
            ("/config", "Show runtime config"),
            ("/thinking [on|off|adaptive]", "Toggle thinking"),
            ("/vim", "Toggle vim mode"),
            ("/image <path>", "Attach image"),
            ("/search <query>", "Search history"),
            ("/index [rebuild]", "Project index"),
            ("/session list|save", "Manage sessions"),
            ("/skill", "Browse & manage skills"),
            ("/plugin", "Browse & manage plugins"),
            ("/mcp", "Browse & manage MCP servers"),
            ("/memory [set|get|delete]", "Project memory"),
            ("/cron [list|delete]", "Scheduled tasks"),
            ("/task [list]", "Task lifecycle"),
            ("/swarm [coordinate]", "Swarm coordination"),
            ("/voice [on|off]", "Voice input"),
            ("/ide [status|connect]", "IDE bridge"),
            ("/vcr [start|stop|list]", "VCR recording"),
            ("/checkpoint [save|list|resume]", "Session checkpoints"),
            ("/hida", "HIDA classification"),
            ("/lsp", "LSP status"),
            ("/cancel", "Cancel generation"),
            ("/exit /quit", "Quit"),
        ]:
            lines.append(f"  {cmd_name:<35s} {desc}")
        chat.add_entry(AssistantText("\n".join(lines)))

    def _cmd_clear(self, args: str) -> None:
        self.query_one(ChatScrollView).remove_children()

    def _cmd_model(self, args: str) -> None:
        chat = self.query_one(ChatScrollView)
        if args:
            import dataclasses
            self._config = dataclasses.replace(self._config, model=args)
            self._init_runtime()
            self.query_one(HeaderBar).model = args
            chat.add_entry(AssistantText(f"Model switched to: {args}"))
        else:
            model = self._config.model if self._config else "(not set)"
            chat.add_entry(AssistantText(f"Current model: {model}"))

    def _cmd_cost(self, args: str) -> None:
        cost = self._cost_tracker.format_cost() if self._cost_tracker else "No cost data"
        self.query_one(ChatScrollView).add_entry(AssistantText(cost))

    def _cmd_cd(self, args: str) -> None:
        chat = self.query_one(ChatScrollView)
        if args:
            new_path = Path(args).expanduser()
            if not new_path.is_absolute():
                new_path = self._cwd / new_path
            new_path = new_path.resolve()
            if new_path.is_dir():
                self._cwd = new_path
                os.chdir(new_path)
                chat.add_entry(AssistantText(f"Working directory: {new_path}"))
            else:
                chat.add_entry(AssistantText(f"Directory not found: {new_path}"))
        else:
            chat.add_entry(AssistantText(f"Current directory: {self._cwd}"))

    def _cmd_budget(self, args: str) -> None:
        chat = self.query_one(ChatScrollView)
        if args:
            try:
                self._budget = int(args)
                chat.add_entry(AssistantText(f"Token budget set: {self._budget:,}"))
            except ValueError:
                chat.add_entry(AssistantText("Usage: /budget <number>"))
        elif self._budget is not None:
            chat.add_entry(AssistantText(f"Current token budget: {self._budget:,}"))
        else:
            chat.add_entry(AssistantText("No budget set."))

    def _cmd_undo(self, args: str) -> None:
        chat = self.query_one(ChatScrollView)
        if not self._checkpoint_mgr:
            chat.add_entry(AssistantText("Not in a git repository — undo not available."))
            return
        if args.strip() == "list":
            cps = self._checkpoint_mgr.list_checkpoints()
            if cps:
                lines = [f"  {cp.id}  {cp.tool_name}  {cp.timestamp[:19]}" for cp in cps]
                chat.add_entry(AssistantText("\n".join(lines)))
            else:
                chat.add_entry(AssistantText("No checkpoints."))
        elif self._checkpoint_mgr.can_undo():
            cp = self._checkpoint_mgr.undo()
            if cp:
                chat.add_entry(AssistantText(f"Undone: {cp.tool_name} ({cp.tool_args_summary[:50]})"))
        else:
            chat.add_entry(AssistantText("Nothing to undo."))

    def _cmd_index(self, args: str) -> None:
        chat = self.query_one(ChatScrollView)
        if args.strip() == "rebuild":
            try:
                from llm_code.runtime.indexer import ProjectIndexer
                self._project_index = ProjectIndexer(self._cwd).build_index()
                idx = self._project_index
                chat.add_entry(AssistantText(f"Index rebuilt: {len(idx.files)} files, {len(idx.symbols)} symbols"))
            except Exception as exc:
                chat.add_entry(AssistantText(f"Index rebuild failed: {exc}"))
        elif self._project_index:
            lines = [f"Files: {len(self._project_index.files)}, Symbols: {len(self._project_index.symbols)}"]
            for s in self._project_index.symbols[:20]:
                lines.append(f"  {s.kind} {s.name} — {s.file}:{s.line}")
            chat.add_entry(AssistantText("\n".join(lines)))
        else:
            chat.add_entry(AssistantText("No index available."))

    def _cmd_thinking(self, args: str) -> None:
        chat = self.query_one(ChatScrollView)
        if args in ("on", "off", "adaptive"):
            import dataclasses
            mode_map = {"on": "enabled", "off": "disabled", "adaptive": "adaptive"}
            new_mode = mode_map[args]
            from llm_code.runtime.config import ThinkingConfig
            new_thinking = ThinkingConfig(mode=new_mode, budget_tokens=self._config.thinking.budget_tokens)
            self._config = dataclasses.replace(self._config, thinking=new_thinking)
            if self._runtime:
                self._runtime._config = self._config
            chat.add_entry(AssistantText(f"Thinking mode: {new_mode}"))
        else:
            current = self._config.thinking.mode if self._config else "unknown"
            chat.add_entry(AssistantText(f"Thinking: {current}\nUsage: /thinking [adaptive|on|off]"))

    def _cmd_vim(self, args: str) -> None:
        chat = self.query_one(ChatScrollView)
        input_bar = self.query_one(InputBar)
        status_bar = self.query_one(StatusBar)
        if input_bar.vim_mode:
            input_bar.vim_mode = ""
            status_bar.vim_mode = ""
            chat.add_entry(AssistantText("Vim mode disabled"))
        else:
            input_bar.vim_mode = "NORMAL"
            status_bar.vim_mode = "NORMAL"
            chat.add_entry(AssistantText("Vim mode enabled"))

    def _cmd_image(self, args: str) -> None:
        chat = self.query_one(ChatScrollView)
        if not args:
            chat.add_entry(AssistantText("Usage: /image <path>"))
            return
        try:
            from llm_code.cli.image import load_image_from_path
            img_path = Path(args).expanduser().resolve()
            img = load_image_from_path(str(img_path))
            self._pending_images.append(img)
            chat.add_entry(AssistantText(f"Image attached: {args}"))
        except FileNotFoundError:
            chat.add_entry(AssistantText(f"Image not found: {args}"))

    def _cmd_lsp(self, args: str) -> None:
        self.query_one(ChatScrollView).add_entry(AssistantText("LSP: not started in this session."))

    def _cmd_cancel(self, args: str) -> None:
        if self._runtime and hasattr(self._runtime, '_cancel'):
            self._runtime._cancel()
        self.query_one(ChatScrollView).add_entry(AssistantText("(cancelled)"))

    def _cmd_search(self, args: str) -> None:
        chat = self.query_one(ChatScrollView)
        if not args or not self._runtime:
            chat.add_entry(AssistantText("Usage: /search <query>"))
            return
        results = []
        for msg in self._runtime.session.messages:
            if args.lower() in str(msg.content).lower():
                results.append(f"  [{msg.role}] {str(msg.content)[:100]}")
        if results:
            chat.add_entry(AssistantText(f"Found {len(results)} matches:\n" + "\n".join(results[:20])))
        else:
            chat.add_entry(AssistantText(f"No matches for: {args}"))

    def _cmd_config(self, args: str) -> None:
        chat = self.query_one(ChatScrollView)
        if not self._config:
            chat.add_entry(AssistantText("No config loaded."))
            return
        lines = [
            f"model: {self._config.model}",
            f"provider: {self._config.provider_base_url or 'default'}",
            f"permission: {self._config.permission_mode}",
            f"thinking: {self._config.thinking.mode}",
        ]
        chat.add_entry(AssistantText("\n".join(lines)))

    def _cmd_session(self, args: str) -> None:
        self.query_one(ChatScrollView).add_entry(AssistantText("Session management: use /session list|save"))

    # ── Voice ─────────────────────────────────────────────────────────

    def _cmd_voice(self, args: str) -> None:
        chat = self.query_one(ChatScrollView)
        arg = args.strip().lower()
        if arg == "on":
            if self._config and getattr(self._config, 'voice', None) and self._config.voice.enabled:
                self._voice_active = True
                chat.add_entry(AssistantText("Voice input enabled"))
            else:
                chat.add_entry(AssistantText("Voice not configured. Set voice.enabled in config."))
        elif arg == "off":
            self._voice_active = False
            chat.add_entry(AssistantText("Voice input disabled"))
        else:
            active = self._voice_active
            chat.add_entry(AssistantText(
                f"Voice: {'active' if active else 'inactive'}\nUsage: /voice [on|off]"
            ))

    # ── Cron ──────────────────────────────────────────────────────────

    def _cmd_cron(self, args: str) -> None:
        chat = self.query_one(ChatScrollView)
        if self._cron_storage is None:
            chat.add_entry(AssistantText("Cron not available."))
            return
        sub = args.strip() if args.strip() else "list"
        if not sub or sub == "list":
            tasks = self._cron_storage.list_all()
            if not tasks:
                chat.add_entry(AssistantText("No scheduled tasks."))
            else:
                lines = [f"Scheduled tasks ({len(tasks)}):"]
                for t in tasks:
                    flags = []
                    if t.recurring:
                        flags.append("recurring")
                    if t.permanent:
                        flags.append("permanent")
                    flag_str = f" [{', '.join(flags)}]" if flags else ""
                    fired = f", last fired: {t.last_fired_at:%Y-%m-%d %H:%M}" if t.last_fired_at else ""
                    lines.append(f"  {t.id}  {t.cron}  \"{t.prompt}\"{flag_str}{fired}")
                chat.add_entry(AssistantText("\n".join(lines)))
        elif sub.startswith("delete "):
            task_id = sub.split(None, 1)[1].strip()
            removed = self._cron_storage.remove(task_id)
            if removed:
                chat.add_entry(AssistantText(f"Deleted task {task_id}"))
            else:
                chat.add_entry(AssistantText(f"Task '{task_id}' not found"))
        elif sub == "add":
            chat.add_entry(AssistantText(
                "Use the cron_create tool to schedule a task:\n"
                "  cron: '0 9 * * *'  (5-field cron expression)\n"
                "  prompt: 'your prompt here'\n"
                "  recurring: true/false\n"
                "  permanent: true/false"
            ))
        else:
            chat.add_entry(AssistantText("Usage: /cron [list|add|delete <id>]"))

    # ── Task ──────────────────────────────────────────────────────────

    def _cmd_task(self, args: str) -> None:
        chat = self.query_one(ChatScrollView)
        parts = args.strip().split(None, 1)
        sub = parts[0] if parts else ""
        if sub in ("new", ""):
            chat.add_entry(AssistantText("Use the task tools directly to create or manage tasks."))
        elif sub == "list":
            if self._task_manager is None:
                chat.add_entry(AssistantText("Task manager not initialized."))
            else:
                try:
                    tasks = self._task_manager.list_tasks(exclude_done=False)
                    if not tasks:
                        chat.add_entry(AssistantText("No tasks found."))
                    else:
                        lines = ["Tasks:"]
                        for t in tasks:
                            lines.append(f"  {t.id}  [{t.status.value:8s}]  {t.title}")
                        chat.add_entry(AssistantText("\n".join(lines)))
                except Exception as exc:
                    chat.add_entry(AssistantText(f"Error listing tasks: {exc}"))
        elif sub in ("verify", "close"):
            chat.add_entry(AssistantText("Use the task tools directly."))
        else:
            chat.add_entry(AssistantText("Usage: /task [new|verify <id>|close <id>|list]"))

    # ── Swarm ─────────────────────────────────────────────────────────

    def _cmd_swarm(self, args: str) -> None:
        chat = self.query_one(ChatScrollView)
        parts = args.strip().split(None, 1)
        sub = parts[0] if parts else ""
        rest = parts[1].strip() if len(parts) > 1 else ""
        if sub == "coordinate":
            if not rest:
                chat.add_entry(AssistantText("Usage: /swarm coordinate <task>"))
                return
            chat.add_entry(AssistantText("Swarm coordination: use the swarm tools directly."))
        else:
            if self._swarm_manager is None:
                chat.add_entry(AssistantText("Swarm: not enabled. Set swarm.enabled=true in config."))
            else:
                chat.add_entry(AssistantText("Swarm: active\nUsage: /swarm coordinate <task>"))

    # ── VCR ───────────────────────────────────────────────────────────

    def _cmd_vcr(self, args: str) -> None:
        chat = self.query_one(ChatScrollView)
        sub = args.strip().split(None, 1)[0] if args.strip() else ""
        if sub == "start":
            if self._vcr_recorder is not None:
                chat.add_entry(AssistantText("VCR recording already active."))
                return
            try:
                import uuid
                from llm_code.runtime.vcr import VCRRecorder
                recordings_dir = Path.home() / ".llm-code" / "recordings"
                recordings_dir.mkdir(parents=True, exist_ok=True)
                session_id = uuid.uuid4().hex[:8]
                path = recordings_dir / f"{session_id}.jsonl"
                self._vcr_recorder = VCRRecorder(path)
                if self._runtime is not None:
                    self._runtime._vcr_recorder = self._vcr_recorder
                chat.add_entry(AssistantText(f"VCR recording started: {path.name}"))
            except Exception as exc:
                chat.add_entry(AssistantText(f"VCR start failed: {exc}"))
        elif sub == "stop":
            if self._vcr_recorder is None:
                chat.add_entry(AssistantText("No active VCR recording."))
                return
            self._vcr_recorder.close()
            self._vcr_recorder = None
            if self._runtime is not None:
                self._runtime._vcr_recorder = None
            chat.add_entry(AssistantText("VCR recording stopped."))
        elif sub == "list":
            recordings_dir = Path.home() / ".llm-code" / "recordings"
            if not recordings_dir.is_dir():
                chat.add_entry(AssistantText("No recordings found."))
                return
            files = sorted(recordings_dir.glob("*.jsonl"))
            if not files:
                chat.add_entry(AssistantText("No recordings found."))
                return
            try:
                from llm_code.runtime.vcr import VCRPlayer
                lines = []
                for f in files:
                    player = VCRPlayer(f)
                    s = player.summary()
                    lines.append(
                        f"  {f.name}  events={s['event_count']}  "
                        f"duration={s['duration']:.1f}s  "
                        f"tools={sum(s['tool_calls'].values())}"
                    )
                chat.add_entry(AssistantText("\n".join(lines)))
            except Exception as exc:
                chat.add_entry(AssistantText(f"VCR list failed: {exc}"))
        else:
            active = "active" if self._vcr_recorder is not None else "inactive"
            chat.add_entry(AssistantText(f"VCR: {active}\nUsage: /vcr start|stop|list"))

    # ── Checkpoint ────────────────────────────────────────────────────

    def _cmd_checkpoint(self, args: str) -> None:
        chat = self.query_one(ChatScrollView)
        try:
            from llm_code.runtime.checkpoint_recovery import CheckpointRecovery
        except ImportError:
            chat.add_entry(AssistantText("Checkpoint recovery not available."))
            return
        checkpoints_dir = Path.home() / ".llm-code" / "checkpoints"
        recovery = CheckpointRecovery(checkpoints_dir)
        parts = args.strip().split(None, 1)
        sub = parts[0].lower() if parts else "list"
        rest = parts[1].strip() if len(parts) > 1 else ""
        if sub == "save":
            if self._runtime is None:
                chat.add_entry(AssistantText("No active session to checkpoint."))
                return
            try:
                path = recovery.save_checkpoint(self._runtime.session)
                chat.add_entry(AssistantText(f"Checkpoint saved: {path}"))
            except Exception as exc:
                chat.add_entry(AssistantText(f"Save failed: {exc}"))
        elif sub in ("list", ""):
            try:
                entries = recovery.list_checkpoints()
                if not entries:
                    chat.add_entry(AssistantText("No checkpoints found."))
                    return
                lines = ["Checkpoints:"]
                for e in entries:
                    lines.append(
                        f"  {e['session_id']}  "
                        f"{e['saved_at'][:19]}  "
                        f"({e['message_count']} msgs)  "
                        f"{e['project_path']}"
                    )
                chat.add_entry(AssistantText("\n".join(lines)))
            except Exception as exc:
                chat.add_entry(AssistantText(f"List failed: {exc}"))
        elif sub == "resume":
            try:
                session_id = rest or None
                if session_id:
                    session = recovery.load_checkpoint(session_id)
                else:
                    session = recovery.detect_last_checkpoint()
                if session is None:
                    chat.add_entry(AssistantText("No checkpoint found to resume."))
                    return
                self._init_runtime()
                chat.add_entry(AssistantText(
                    f"Resumed session {session.id} ({len(session.messages)} messages)"
                ))
            except Exception as exc:
                chat.add_entry(AssistantText(f"Resume failed: {exc}"))
        else:
            chat.add_entry(AssistantText("Usage: /checkpoint [save|list|resume [session_id]]"))

    # ── Memory ────────────────────────────────────────────────────────

    def _cmd_memory(self, args: str) -> None:
        chat = self.query_one(ChatScrollView)
        if not self._memory:
            chat.add_entry(AssistantText("Memory not initialized."))
            return
        parts = args.strip().split(None, 2)
        sub = parts[0] if parts else ""
        try:
            if sub == "set" and len(parts) > 2:
                self._memory.store(parts[1], parts[2])
                chat.add_entry(AssistantText(f"Stored: {parts[1]}"))
            elif sub == "get" and len(parts) > 1:
                val = self._memory.recall(parts[1])
                if val:
                    chat.add_entry(AssistantText(str(val)))
                else:
                    chat.add_entry(AssistantText(f"Key not found: {parts[1]}"))
            elif sub == "delete" and len(parts) > 1:
                self._memory.delete(parts[1])
                chat.add_entry(AssistantText(f"Deleted: {parts[1]}"))
            elif sub == "consolidate":
                chat.add_entry(AssistantText("Use --lite mode for consolidate (requires async)."))
            elif sub == "history":
                summaries = self._memory.load_consolidated_summaries(limit=5)
                if not summaries:
                    chat.add_entry(AssistantText("No consolidated memories yet."))
                else:
                    lines = [f"Consolidated Memories ({len(summaries)} most recent)"]
                    for i, s in enumerate(summaries):
                        preview = "\n".join(s.strip().splitlines()[:3])
                        lines.append(f"  #{i+1} {preview}")
                    chat.add_entry(AssistantText("\n".join(lines)))
            else:
                entries = self._memory.get_all()
                lines = [f"Memory ({len(entries)} entries)"]
                for k, v in entries.items():
                    lines.append(f"  {k}: {v.value[:60]}")
                if not entries:
                    lines.append("  No memories stored.")
                chat.add_entry(AssistantText("\n".join(lines)))
        except Exception as exc:
            chat.add_entry(AssistantText(f"Memory error: {exc}"))

    # ── MCP ───────────────────────────────────────────────────────────

    def _cmd_mcp(self, args: str) -> None:
        chat = self.query_one(ChatScrollView)
        parts = args.strip().split(None, 1)
        sub = parts[0] if parts else ""
        subargs = parts[1] if len(parts) > 1 else ""
        if sub == "install" and subargs:
            pkg = subargs.strip()
            short_name = pkg.split("/")[-1] if "/" in pkg else pkg
            # Write to config.json
            config_path = Path.home() / ".llm-code" / "config.json"
            try:
                import json
                config_data: dict = {}
                if config_path.exists():
                    config_data = json.loads(config_path.read_text())
                mcp_servers = config_data.setdefault("mcp_servers", {})
                mcp_servers[short_name] = {"command": "npx", "args": ["-y", pkg]}
                config_path.parent.mkdir(parents=True, exist_ok=True)
                config_path.write_text(json.dumps(config_data, indent=2) + "\n")
                # Update in-memory config so marketplace reflects the change
                if self._config is not None:
                    import dataclasses
                    current_servers = dict(self._config.mcp_servers or {})
                    current_servers[short_name] = {"command": "npx", "args": ["-y", pkg]}
                    self._config = dataclasses.replace(self._config, mcp_servers=current_servers)
                chat.add_entry(AssistantText(f"Added {short_name} to config. Starting server..."))
                # Hot-start the MCP server without restart
                self._hot_start_mcp(short_name, {"command": "npx", "args": ["-y", pkg]})
            except Exception as exc:
                chat.add_entry(AssistantText(f"Install failed: {exc}"))
        elif sub == "remove" and subargs:
            name = subargs.strip()
            config_path = Path.home() / ".llm-code" / "config.json"
            try:
                import json
                if config_path.exists():
                    config_data = json.loads(config_path.read_text())
                    mcp_servers = config_data.get("mcp_servers", {})
                    if name in mcp_servers:
                        del mcp_servers[name]
                        config_path.write_text(json.dumps(config_data, indent=2) + "\n")
                        # Update in-memory config
                        if self._config is not None:
                            import dataclasses
                            current = dict(self._config.mcp_servers or {})
                            current.pop(name, None)
                            self._config = dataclasses.replace(self._config, mcp_servers=current)
                        chat.add_entry(AssistantText(f"Removed {name} from config."))
                    else:
                        chat.add_entry(AssistantText(f"MCP server '{name}' not found in config."))
                else:
                    chat.add_entry(AssistantText("No config file found."))
            except Exception as exc:
                chat.add_entry(AssistantText(f"Remove failed: {exc}"))
        else:
            # Open interactive MCP marketplace browser
            from llm_code.tui.marketplace import MarketplaceBrowser, MarketplaceItem

            items: list[MarketplaceItem] = []
            configured: set[str] = set()

            # Configured MCP servers
            servers = {}
            if self._config and self._config.mcp_servers:
                servers = self._config.mcp_servers
            for name, cfg in servers.items():
                configured.add(name)
                cmd = ""
                if isinstance(cfg, dict):
                    cmd = f"{cfg.get('command', '')} {' '.join(cfg.get('args', []))}".strip()
                items.append(MarketplaceItem(
                    name=name,
                    description=cmd or "(configured)",
                    source="configured",
                    installed=True,
                    enabled=True,
                    repo="",
                ))

            # Known MCP servers from npm registry (popular ones)
            known_mcp = [
                ("@anthropic/mcp-server-filesystem", "File system access via MCP"),
                ("@anthropic/mcp-server-github", "GitHub API integration via MCP"),
                ("@anthropic/mcp-server-slack", "Slack integration via MCP"),
                ("@anthropic/mcp-server-google-maps", "Google Maps API via MCP"),
                ("@anthropic/mcp-server-puppeteer", "Browser automation via MCP"),
                ("@anthropic/mcp-server-memory", "Persistent memory via MCP"),
                ("@anthropic/mcp-server-postgres", "PostgreSQL access via MCP"),
                ("@anthropic/mcp-server-sqlite", "SQLite database via MCP"),
                ("@modelcontextprotocol/server-brave-search", "Brave search via MCP"),
                ("@modelcontextprotocol/server-fetch", "HTTP fetch via MCP"),
                ("tavily-mcp", "Tavily AI search via MCP"),
                ("@supabase/mcp-server-supabase", "Supabase database via MCP"),
                ("context7-mcp", "Context7 documentation lookup via MCP"),
            ]
            for pkg_name, desc in known_mcp:
                short = pkg_name.split("/")[-1] if "/" in pkg_name else pkg_name
                if short not in configured and pkg_name not in configured:
                    items.append(MarketplaceItem(
                        name=pkg_name,
                        description=desc,
                        source="npm",
                        installed=False,
                        repo="",
                        extra="npx",
                    ))

            browser = MarketplaceBrowser("MCP Server Marketplace", items)
            self.push_screen(browser)

    # ── IDE ───────────────────────────────────────────────────────────

    def _cmd_ide(self, args: str) -> None:
        chat = self.query_one(ChatScrollView)
        sub = args.strip().lower()
        if sub == "connect":
            chat.add_entry(AssistantText("IDE bridge starts automatically when configured. Set ide.enabled=true in config."))
            return
        # status (default)
        if self._ide_bridge is None:
            chat.add_entry(AssistantText("IDE integration is disabled. Set ide.enabled=true in config."))
            return
        try:
            if self._ide_bridge.is_connected:
                ides = self._ide_bridge._server.connected_ides if self._ide_bridge._server else []
                names = ", ".join(ide.name for ide in ides) if ides else "unknown"
                chat.add_entry(AssistantText(f"IDE connected: {names}"))
            else:
                port = self._ide_bridge._config.port
                chat.add_entry(AssistantText(f"IDE bridge listening on port {port}, no IDE connected."))
        except Exception as exc:
            chat.add_entry(AssistantText(f"IDE status error: {exc}"))

    # ── HIDA ──────────────────────────────────────────────────────────

    def _cmd_hida(self, args: str) -> None:
        chat = self.query_one(ChatScrollView)
        if self._runtime and hasattr(self._runtime, "_last_hida_profile"):
            profile = self._runtime._last_hida_profile
            if profile is not None:
                try:
                    from llm_code.hida.engine import HidaEngine
                    engine = HidaEngine()
                    summary = engine.build_summary(profile)
                    chat.add_entry(AssistantText(f"HIDA: {summary}"))
                except Exception as exc:
                    chat.add_entry(AssistantText(f"HIDA: {exc}"))
            else:
                hida_enabled = (
                    getattr(self._config, "hida", None) and self._config.hida.enabled
                )
                status = "enabled" if hida_enabled else "disabled"
                chat.add_entry(AssistantText(f"HIDA: {status}, no classification yet"))
        else:
            chat.add_entry(AssistantText("HIDA: not initialized"))

    # ── Skill ─────────────────────────────────────────────────────────

    def _cmd_skill(self, args: str) -> None:
        chat = self.query_one(ChatScrollView)
        parts = args.strip().split(None, 1)
        sub = parts[0] if parts else ""
        subargs = parts[1] if len(parts) > 1 else ""
        if sub == "install" and subargs:
            source = subargs.strip()
            if not self._is_valid_repo(source):
                chat.add_entry(AssistantText("Usage: /skill install owner/repo"))
                return
            import tempfile
            repo = source.replace("https://github.com/", "").rstrip("/")
            name = repo.split("/")[-1]
            dest = Path.home() / ".llm-code" / "skills" / name
            if dest.exists():
                shutil.rmtree(dest)
            chat.add_entry(AssistantText(f"Cloning {repo}..."))
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    result = subprocess.run(
                        ["git", "clone", "--depth", "1",
                         f"https://github.com/{repo}.git", tmp],
                        capture_output=True, text=True, timeout=30,
                    )
                    if result.returncode == 0:
                        skills_src = Path(tmp) / "skills"
                        if skills_src.is_dir():
                            shutil.copytree(skills_src, dest)
                        else:
                            shutil.copytree(tmp, dest)
                        chat.add_entry(AssistantText(f"Installed {name}. Restart to activate."))
                    else:
                        logger.warning("Skill clone failed for %s: %s", repo, result.stderr[:200])
                        chat.add_entry(AssistantText(f"Clone failed. Check the repository URL."))
            except Exception as exc:
                chat.add_entry(AssistantText(f"Install failed: {exc}"))
        elif sub == "enable" and subargs:
            if not self._is_safe_name(subargs):
                chat.add_entry(AssistantText("Invalid skill name."))
                return
            marker = Path.home() / ".llm-code" / "skills" / subargs / ".disabled"
            marker.unlink(missing_ok=True)
            chat.add_entry(AssistantText(f"Enabled {subargs}"))
        elif sub == "disable" and subargs:
            if not self._is_safe_name(subargs):
                chat.add_entry(AssistantText("Invalid skill name."))
                return
            marker = Path.home() / ".llm-code" / "skills" / subargs / ".disabled"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
            chat.add_entry(AssistantText(f"Disabled {subargs}"))
        elif sub == "remove" and subargs:
            if not self._is_safe_name(subargs):
                chat.add_entry(AssistantText("Invalid skill name."))
                return
            d = Path.home() / ".llm-code" / "skills" / subargs
            if d.is_dir():
                shutil.rmtree(d)
                chat.add_entry(AssistantText(f"Removed {subargs}"))
            else:
                chat.add_entry(AssistantText(f"Not found: {subargs}"))
        else:
            # Open interactive marketplace browser
            from llm_code.tui.marketplace import MarketplaceBrowser, MarketplaceItem
            from llm_code.marketplace.builtin_registry import get_all_known_plugins

            items: list[MarketplaceItem] = []
            installed_names: set[str] = set()

            # Installed skills (from runtime)
            all_skills: list = []
            if self._skills:
                all_skills = list(self._skills.auto_skills) + list(self._skills.command_skills)
            for s in all_skills:
                installed_names.add(s.name)
                tokens = len(s.content) // 4
                mode = "auto" if s.auto else f"/{s.trigger}"
                items.append(MarketplaceItem(
                    name=s.name,
                    description=f"{mode}  ~{tokens} tokens",
                    source="installed",
                    installed=True,
                    enabled=not (Path.home() / ".llm-code" / "skills" / s.name / ".disabled").exists(),
                    repo="",
                    extra=mode,
                ))

            # Installed plugins (check filesystem for newly installed)
            try:
                from llm_code.marketplace.installer import PluginInstaller
                pi = PluginInstaller(Path.home() / ".llm-code" / "plugins")
                for p in pi.list_installed():
                    if p.manifest.name not in installed_names:
                        installed_names.add(p.manifest.name)
                        items.append(MarketplaceItem(
                            name=p.manifest.name,
                            description=getattr(p.manifest, "description", ""),
                            source="installed",
                            installed=True,
                            enabled=p.enabled,
                            repo="",
                            extra=f"v{p.manifest.version}",
                        ))
            except Exception:
                pass

            # Marketplace plugins with skills — not yet installed
            for p in get_all_known_plugins():
                if p.get("skills", 0) > 0 and p["name"] not in installed_names:
                    items.append(MarketplaceItem(
                        name=p["name"],
                        description=p.get("desc", ""),
                        source=p.get("source", "official"),
                        installed=False,
                        repo=p.get("repo", ""),
                        extra=f"{p['skills']} skills",
                    ))

            browser = MarketplaceBrowser("Skills Marketplace", items)
            self.push_screen(browser)

    # ── Plugin ────────────────────────────────────────────────────────

    def _cmd_plugin(self, args: str) -> None:
        chat = self.query_one(ChatScrollView)
        parts = args.strip().split(None, 1)
        sub = parts[0] if parts else ""
        subargs = parts[1] if len(parts) > 1 else ""
        try:
            from llm_code.marketplace.installer import PluginInstaller
            installer = PluginInstaller(Path.home() / ".llm-code" / "plugins")
        except ImportError:
            chat.add_entry(AssistantText("Plugin system not available."))
            return
        if sub == "install" and subargs:
            source = subargs.strip()
            if not self._is_valid_repo(source):
                chat.add_entry(AssistantText("Usage: /plugin install owner/repo"))
                return
            repo = source.replace("https://github.com/", "").rstrip("/")
            name = repo.split("/")[-1]
            dest = Path.home() / ".llm-code" / "plugins" / name
            if dest.exists():
                shutil.rmtree(dest)
            chat.add_entry(AssistantText(f"Cloning {repo}..."))
            try:
                result = subprocess.run(
                    ["git", "clone", "--depth", "1",
                     f"https://github.com/{repo}.git", str(dest)],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode == 0:
                    installer.enable(name)
                    chat.add_entry(AssistantText(f"Installed {name}. Restart to activate."))
                else:
                    logger.warning("Plugin clone failed for %s: %s", repo, result.stderr[:200])
                    chat.add_entry(AssistantText("Clone failed. Check the repository URL."))
            except Exception as exc:
                chat.add_entry(AssistantText(f"Install failed: {exc}"))
        elif sub == "enable" and subargs:
            if not self._is_safe_name(subargs):
                chat.add_entry(AssistantText("Invalid plugin name."))
                return
            try:
                installer.enable(subargs)
                chat.add_entry(AssistantText(f"Enabled {subargs}"))
            except Exception as exc:
                chat.add_entry(AssistantText(f"Enable failed: {exc}"))
        elif sub == "disable" and subargs:
            if not self._is_safe_name(subargs):
                chat.add_entry(AssistantText("Invalid plugin name."))
                return
            try:
                installer.disable(subargs)
                chat.add_entry(AssistantText(f"Disabled {subargs}"))
            except Exception as exc:
                chat.add_entry(AssistantText(f"Disable failed: {exc}"))
        elif sub in ("remove", "uninstall") and subargs:
            if not self._is_safe_name(subargs):
                chat.add_entry(AssistantText("Invalid plugin name."))
                return
            try:
                installer.uninstall(subargs)
                chat.add_entry(AssistantText(f"Removed {subargs}"))
            except Exception as exc:
                chat.add_entry(AssistantText(f"Remove failed: {exc}"))
        else:
            # Open interactive marketplace browser
            from llm_code.tui.marketplace import MarketplaceBrowser, MarketplaceItem
            from llm_code.marketplace.builtin_registry import get_all_known_plugins

            items: list[MarketplaceItem] = []

            # Installed plugins first
            installed_names: set[str] = set()
            try:
                installed = installer.list_installed()
                for p in installed:
                    installed_names.add(p.manifest.name)
                    items.append(MarketplaceItem(
                        name=p.manifest.name,
                        description=getattr(p.manifest, "description", ""),
                        source="installed",
                        installed=True,
                        enabled=p.enabled,
                        repo="",
                        extra=f"v{p.manifest.version}",
                    ))
            except Exception:
                pass

            # Known marketplace plugins not yet installed
            for p in get_all_known_plugins():
                if p["name"] not in installed_names:
                    skills_count = p.get("skills", 0)
                    extra = f"{skills_count} skills" if skills_count else ""
                    items.append(MarketplaceItem(
                        name=p["name"],
                        description=p.get("desc", ""),
                        source=p.get("source", "official"),
                        installed=False,
                        repo=p.get("repo", ""),
                        extra=extra,
                    ))

            browser = MarketplaceBrowser("Plugin Marketplace", items)
            self.push_screen(browser)

    # ── Marketplace ItemAction handler ────────────────────────────────

    def on_marketplace_browser_item_action(
        self, event: "MarketplaceBrowser.ItemAction"
    ) -> None:
        """Handle marketplace item selection (install/enable/disable/remove)."""
        from llm_code.tui.marketplace import MarketplaceBrowser
        from llm_code.tui.chat_view import AssistantText

        chat = self.query_one(ChatScrollView)
        item = event.item
        action = event.action

        if action == "install":
            if item.source == "npm":
                # MCP server install — show config instructions
                self._cmd_mcp(f"install {item.name}")
            elif item.repo:
                if item.source in ("official", "community"):
                    self._cmd_plugin(f"install {item.repo}")
                else:
                    self._cmd_skill(f"install {item.repo}")
            else:
                chat.add_entry(AssistantText(
                    f"No repo URL available for {item.name}. Install manually."
                ))
        elif action == "enable":
            if item.source in ("official", "community", "installed"):
                self._cmd_plugin(f"enable {item.name}")
            else:
                self._cmd_skill(f"enable {item.name}")
        elif action == "disable":
            if item.source in ("official", "community", "installed"):
                self._cmd_plugin(f"disable {item.name}")
            else:
                self._cmd_skill(f"disable {item.name}")
        elif action == "remove":
            if item.source in ("official", "community", "installed"):
                self._cmd_plugin(f"remove {item.name}")
            else:
                self._cmd_skill(f"remove {item.name}")
