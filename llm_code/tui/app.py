# llm_code/tui/app.py
"""LLMCodeTUI — Textual fullscreen app composing all widgets."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding

from llm_code.tui.chat_view import ChatScrollView, UserMessage, AssistantText, SkillBadge
from llm_code.tui.header_bar import HeaderBar
from llm_code.tui.input_bar import InputBar
from llm_code.tui.status_bar import StatusBar
from llm_code.tui.theme import APP_CSS
from llm_code.logging import get_logger

if TYPE_CHECKING:
    from llm_code.tui.marketplace import MarketplaceBrowser  # noqa: F811

logger = get_logger(__name__)


def _is_cjk_dominant(text: str) -> bool:
    """Return True if the text contains any CJK characters.

    Used to pick message language in the empty-response handler. We use a
    "any CJK present" rule rather than a percentage threshold because
    multilingual users routinely mix English technical terms into
    otherwise-Chinese prompts (e.g. "解釋 quicksort 演算法",
    "為什麼 Python 的 list 是 O(1) append"). If the user typed even one
    Chinese character, they will understand a Chinese diagnostic message.
    Pure English users never emit CJK characters, so they stay English.
    """
    if not text:
        return False
    for ch in text:
        code = ord(ch)
        # CJK Unified Ideographs, CJK Extension A, Hiragana, Katakana,
        # Hangul Syllables, CJK Symbols and Punctuation, fullwidth forms
        if (
            0x3000 <= code <= 0x303F
            or 0x3040 <= code <= 0x309F
            or 0x30A0 <= code <= 0x30FF
            or 0x3400 <= code <= 0x4DBF
            or 0x4E00 <= code <= 0x9FFF
            or 0xAC00 <= code <= 0xD7AF
            or 0xFF00 <= code <= 0xFFEF
        ):
            return True
    return False


_EMPTY_RESPONSE_TOOL_CALL_EN = (
    "(The model tried to invoke a tool to answer this but produced no visible reply. "
    "If this is a general-knowledge or chitchat query, try rephrasing to ask for a "
    "direct answer — e.g. add \"answer directly\" or \"don't use tools\".)"
)
_EMPTY_RESPONSE_TOOL_CALL_ZH = (
    "(模型嘗試呼叫工具回答這個問題,但沒有產生可見回覆。"
    "如果這是一般知識/閒聊查詢,請試著更明確地表達你想要直接的回答,"
    "例如加上「請直接回答」或「不要用工具」。)"
)
_EMPTY_RESPONSE_THINKING_EN = (
    "(The model produced no visible reply — thinking may have exhausted the "
    "output token budget. Try rephrasing or increasing the context window.)"
)
_EMPTY_RESPONSE_THINKING_ZH = (
    "(模型沒有產生任何回應 — 可能 thinking 用光輸出 token。"
    "試試重新表達或加長 context window。)"
)


def _session_is_cjk(user_input: str, session_messages: Any = None) -> bool:
    """Decide whether to use CJK messages, based on the current input AND
    any prior CJK content in the session.

    A user who said "今日熱門新聞三則" earlier and then types "1" or "ok"
    is still a CJK user — we shouldn't flip back to English just because
    the latest input has no CJK characters. We check the latest input
    first; if not CJK, scan recent session messages for ANY CJK char.
    """
    if _is_cjk_dominant(user_input):
        return True
    if session_messages is None:
        return False
    # Walk recent messages (cap at 20 to bound work) and check user
    # text content for CJK. We deliberately ignore assistant text to
    # avoid the CLI's own English status messages skewing the verdict.
    try:
        recent = list(session_messages)[-20:]
    except TypeError:
        return False
    for msg in recent:
        if getattr(msg, "role", None) != "user":
            continue
        content = getattr(msg, "content", None) or ()
        for block in content:
            text = getattr(block, "text", None)
            if text and _is_cjk_dominant(text):
                return True
    return False


def _empty_response_message(
    *,
    saw_tool_call: bool,
    user_input: str,
    session_messages: Any = None,
) -> str:
    """Pick the right empty-response diagnostic message, matching the
    user's language (CJK vs non-CJK), looking at the current input AND
    the recent session history so a Chinese user typing a short follow-up
    like "1" still sees Chinese."""
    zh = _session_is_cjk(user_input, session_messages)
    if saw_tool_call:
        return _EMPTY_RESPONSE_TOOL_CALL_ZH if zh else _EMPTY_RESPONSE_TOOL_CALL_EN
    return _EMPTY_RESPONSE_THINKING_ZH if zh else _EMPTY_RESPONSE_THINKING_EN


def _register_core_tools(registry: "ToolRegistry", config: "RuntimeConfig") -> None:
    """Register the collaborator-free core tool set into ``registry``.

    Shared between the TUI boot path and headless callers (like
    ``llm_code.cli.oneshot.run_quick_mode``) so both exercise the same
    file / shell / search / git tool set. Tools that depend on
    instance-scoped collaborators (MemoryStore, SkillSet, SwarmManager,
    IDEBridge, LspManager, etc.) are intentionally NOT registered here
    — the TUI boot path registers those separately after this helper
    runs.
    """
    from llm_code.tools.bash import BashTool
    from llm_code.tools.edit_file import EditFileTool
    from llm_code.tools.git_tools import (
        GitBranchTool,
        GitCommitTool,
        GitDiffTool,
        GitLogTool,
        GitPushTool,
        GitStashTool,
        GitStatusTool,
    )
    from llm_code.tools.glob_search import GlobSearchTool
    from llm_code.tools.grep_search import GrepSearchTool
    from llm_code.tools.notebook_edit import NotebookEditTool
    from llm_code.tools.notebook_read import NotebookReadTool
    from llm_code.tools.read_file import ReadFileTool
    from llm_code.tools.web_fetch import WebFetchTool
    from llm_code.tools.web_search import WebSearchTool
    from llm_code.tools.write_file import WriteFileTool

    base_url = config.provider_base_url or ""
    is_local = any(
        h in base_url
        for h in ("localhost", "127.0.0.1", "0.0.0.0", "192.168.", "10.", "172.")
    )
    bash_timeout = 0 if is_local else 30  # 0 = no timeout for local models

    for tool in (
        ReadFileTool(),
        WriteFileTool(),
        EditFileTool(),
        BashTool(default_timeout=bash_timeout, compress_output=config.output_compression),
        GlobSearchTool(),
        GrepSearchTool(),
        NotebookReadTool(),
        NotebookEditTool(),
        WebFetchTool(),
        WebSearchTool(),
    ):
        try:
            registry.register(tool)
        except ValueError:
            pass

    for cls in (
        GitStatusTool,
        GitDiffTool,
        GitLogTool,
        GitCommitTool,
        GitPushTool,
        GitStashTool,
        GitBranchTool,
    ):
        try:
            registry.register(cls())
        except ValueError:
            pass


class LLMCodeTUI(App):  # noqa: E302
    @classmethod
    def _register_core_tools_into(
        cls, registry: "ToolRegistry", config: "RuntimeConfig"
    ) -> None:
        """Classmethod facade over the module-level ``_register_core_tools``
        helper. Kept on the class so headless callers can do
        ``LLMCodeTUI._register_core_tools_into(reg, cfg)`` without
        instantiating a TUI."""
        _register_core_tools(registry, config)

    """Fullscreen TUI matching Claude Code's visual experience."""

    TITLE = "llm-code"
    CSS = APP_CSS
    BINDINGS = [
        Binding("ctrl+d", "quit_app", "Quit"),
        Binding("pageup", "scroll_chat_up", "Scroll Up"),
        Binding("pagedown", "scroll_chat_down", "Scroll Down"),
        Binding("shift+up", "scroll_chat_up", "Scroll Up"),
        Binding("shift+down", "scroll_chat_down", "Scroll Down"),
        # priority=True overrides Textual's screen-level shift+tab → focus_previous
        Binding("shift+tab", "cycle_agent", "Cycle Agent", priority=True),
        Binding("ctrl+y", "cycle_agent", "Cycle Agent"),  # alternate keybinding
    ]

    def __init__(
        self,
        config: Any = None,
        cwd: Path | None = None,
        budget: int | None = None,
        initial_mode: str | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._cwd = cwd or Path.cwd()
        self._budget = budget
        self._initial_mode = initial_mode
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
        self._permission_pending = False
        self._mcp_approval_pending = False
        self._mcp_approval_widget = None
        self._pending_images: list = []
        self._plan_mode: bool = False
        self._voice_active = False
        self._vcr_recorder = None
        self._interrupt_pending: bool = False
        self._last_interrupt_time: float = 0.0
        self._analysis_context: str | None = None
        self._context_warned: bool = False  # one-shot 80% warning

    def compose(self) -> ComposeResult:
        from llm_code.tui.chat_widgets import RateLimitBar
        yield HeaderBar(id="header-bar")
        yield ChatScrollView(id="chat-view")
        yield InputBar()
        yield RateLimitBar()
        yield StatusBar(id="status-bar")

    def on_mount(self) -> None:
        self._init_runtime()
        header = self.query_one(HeaderBar)
        if self._config:
            header.model = getattr(self._config, "model", "")
        header.project = self._cwd.name
        header.branch = self._detect_branch()
        self._render_welcome()
        # Detect local provider and update status bar
        if self._config and self._config.provider_base_url:
            url = self._config.provider_base_url
            status = self.query_one(StatusBar)
            status.is_local = "localhost" in url or "127.0.0.1" in url or "0.0.0.0" in url
        # Populate reactive fields from runtime + cwd (model, cwd, branch, mode)
        try:
            status = self.query_one(StatusBar)
            if self._config and getattr(self._config, "model", None):
                status.model = str(self._config.model)
            status.cwd_basename = self._cwd.name
            status.refresh_git_branch(self._cwd)
            if self._config and getattr(self._config, "permission_mode", None):
                status.permission_mode = str(self._config.permission_mode)
        except Exception:
            pass
        # Initialize context window meter
        try:
            status = self.query_one(StatusBar)
            limit = 0
            if self._config is not None:
                limit = int(getattr(self._config, "compact_after_tokens", 0) or 0)
            if limit <= 0:
                limit = 128_000
            status.context_limit = limit
        except Exception:
            pass
        # Periodic task count polling for status bar
        self.set_interval(3.0, self._poll_bg_tasks)
        # Wire RateLimitBar to cost tracker (hide when no rate-limit info)
        try:
            from llm_code.tui.chat_widgets import RateLimitBar as _RLB
            rl_bar = self.query_one(_RLB)
            rl_bar.set_tracker(self._cost_tracker)
            self._refresh_rate_limit_bar()
            self.set_interval(5.0, self._refresh_rate_limit_bar)
        except Exception:
            pass
        # Apply initial mode from CLI --mode flag
        if self._initial_mode:
            self._cmd_mode(self._initial_mode)
        # Focus input bar so it receives key events
        self.query_one(InputBar).focus()
        # Register SIGINT handler for clean interrupt (Ctrl+C)
        import signal

        def _sigint_handler(signum, frame):
            self.call_from_thread(self._handle_interrupt)

        signal.signal(signal.SIGINT, _sigint_handler)
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
        paste_key = "Cmd+V to paste" if sys.platform == "darwin" else "Ctrl+V to paste"

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
            ("Multiline", "Shift+Enter or Ctrl+J"),
            ("Images", paste_key),
            ("Scroll", "PageUp/Down · Shift+↑/↓"),
            ("Cycle agent", "Shift+Tab or Ctrl+Y (build/plan/suggest)"),
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

    def _install_from_marketplace(self, name: str, repo: str, subdir: str) -> None:
        """Install a plugin from a marketplace repo subdirectory."""
        import tempfile
        chat = self.query_one(ChatScrollView)
        dest = Path.home() / ".llmcode" / "plugins" / name
        if dest.exists():
            shutil.rmtree(dest)
        chat.add_entry(AssistantText(f"Installing {name} from {repo}..."))
        try:
            with tempfile.TemporaryDirectory() as tmp:
                result = subprocess.run(
                    ["git", "clone", "--depth", "1",
                     f"https://github.com/{repo}.git", tmp],
                    capture_output=True, text=True, timeout=60,
                )
                if result.returncode != 0:
                    chat.add_entry(AssistantText(f"Clone failed: {result.stderr[:120]}"))
                    return
                src = Path(tmp) / subdir
                if not src.is_dir():
                    chat.add_entry(AssistantText(f"Plugin directory not found: {subdir}"))
                    return
                shutil.copytree(src, dest)
                # Register in plugin state so it shows as installed
                try:
                    from llm_code.marketplace.installer import PluginInstaller
                    installer = PluginInstaller(Path.home() / ".llmcode" / "plugins")
                    installer.enable(name)
                except Exception:
                    pass
                self._reload_skills()
                chat.add_entry(AssistantText(f"Installed {name}. Activated."))
        except Exception as exc:
            chat.add_entry(AssistantText(f"Install failed: {exc}"))

    def _detect_branch(self) -> str:
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=self._cwd, capture_output=True, text=True, timeout=3,
            )
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""

    def _handle_interrupt(self) -> None:
        """Handle Ctrl+C: first press saves checkpoint, second force exits."""
        import time as _time

        now = _time.monotonic()
        status = self.query_one(StatusBar)
        chat = self.query_one(ChatScrollView)

        # If not streaming, exit immediately
        if not status.is_streaming:
            self.exit()
            return

        # Second Ctrl+C within 2 seconds: force exit
        if self._interrupt_pending and (now - self._last_interrupt_time) < 2.0:
            chat.add_entry(AssistantText("Goodbye."))
            self.exit()
            return

        # First Ctrl+C while streaming: save checkpoint and prompt
        self._interrupt_pending = True
        self._last_interrupt_time = now

        session_id = ""
        if self._runtime is not None:
            try:
                from llm_code.runtime.checkpoint_recovery import CheckpointRecovery
                recovery = CheckpointRecovery(
                    Path.home() / ".llmcode" / "checkpoints"
                )
                _path = recovery.save_checkpoint(self._runtime.session)
                session_id = self._runtime.session.id
            except Exception:
                pass

        resume_hint = (
            f"\n  Resume with: llm-code --resume {session_id}" if session_id else ""
        )
        chat.add_entry(AssistantText(
            f"\u23f8 Session paused and saved.{resume_hint}\n"
            f"  Press Ctrl+C again to quit immediately."
        ))

    def _reload_skills(self) -> None:
        """(Re)load skills from all configured directories."""
        import logging
        _log = logging.getLogger(__name__)
        try:
            from llm_code.runtime.skills import SkillLoader
            from llm_code.marketplace.installer import PluginInstaller
            # Built-in skills shipped with llm-code (oh-my-opencode-skills, etc.)
            import llm_code.marketplace as _mkt_pkg
            _builtin_root = Path(_mkt_pkg.__file__).parent / "builtin"
            skill_dirs: list[Path] = []
            if _builtin_root.is_dir():
                for plugin in sorted(_builtin_root.iterdir()):
                    sp = plugin / "skills"
                    if sp.is_dir():
                        skill_dirs.append(sp)
            skill_dirs.extend([
                Path.home() / ".llmcode" / "skills",
                self._cwd / ".llmcode" / "skills",
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
            self._skills = SkillLoader().load_from_dirs(skill_dirs)
            _log.info(
                "skill load: dirs=%d auto=%d command=%d",
                len(skill_dirs),
                len(self._skills.auto_skills) if self._skills else 0,
                len(self._skills.command_skills) if self._skills else 0,
            )
        except Exception as e:
            _log.warning("skill load failed: %r", e, exc_info=True)
            self._skills = None

    def _init_runtime(self) -> None:
        """Initialize the conversation runtime."""
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

        # Register core tools — collaborator-free set shared with the
        # headless ``run_quick_mode`` path so both exercise the same
        # tools. Instance-scoped tools (memory, skills, swarm, IDE, LSP,
        # etc.) are registered further down.
        self._tool_reg = ToolRegistry()
        LLMCodeTUI._register_core_tools_into(self._tool_reg, self._config)

        # Register AgentTool with a lazy factory closure that captures `self`
        # so the parent runtime — built later in startup — is reachable.
        try:
            from llm_code.runtime.subagent_factory import make_subagent_runtime
            from llm_code.tools.agent import AgentTool

            def _subagent_factory(model=None, role=None):
                parent_runtime = getattr(self, "_runtime", None)
                if parent_runtime is None:
                    raise RuntimeError(
                        "AgentTool invoked before parent runtime initialized"
                    )
                return make_subagent_runtime(parent_runtime, role, model)

            if self._tool_reg.get("agent") is None:
                self._tool_reg.register(AgentTool(
                    runtime_factory=_subagent_factory,
                    max_depth=3,
                    current_depth=0,
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
            recovery_checkpoint = CheckpointRecovery(Path.home() / ".llmcode" / "checkpoints")
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
        self._reload_skills()

        # Memory (legacy key-value store)
        try:
            from llm_code.runtime.memory import MemoryStore
            memory_dir = Path.home() / ".llmcode" / "memory"
            self._memory = MemoryStore(memory_dir, self._cwd)
        except Exception:
            self._memory = None

        # Typed memory (4-type taxonomy)
        self._typed_memory = None
        try:
            import hashlib
            from llm_code.runtime.memory_taxonomy import TypedMemoryStore
            project_hash = hashlib.sha256(str(self._cwd).encode()).hexdigest()[:8]
            typed_dir = Path.home() / ".llmcode" / "memory" / project_hash / "typed"
            self._typed_memory = TypedMemoryStore(typed_dir)
            # Auto-migrate legacy memory if typed store is empty
            if self._memory and not self._typed_memory.list_all():
                legacy_file = Path.home() / ".llmcode" / "memory" / project_hash / "memory.json"
                if legacy_file.exists():
                    self._typed_memory.migrate_from_legacy(legacy_file)
        except Exception:
            pass

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

        # Register skill_load tool — lets LLM actively load skills (complement to router)
        try:
            from llm_code.tools.skill_load import SkillLoadTool
            if self._skills is not None:
                self._tool_reg.register(SkillLoadTool(self._skills))
        except (ImportError, ValueError):
            pass

        # Register cron tools
        try:
            from llm_code.cron.storage import CronStorage
            from llm_code.tools.cron_create import CronCreateTool
            from llm_code.tools.cron_list import CronListTool
            from llm_code.tools.cron_delete import CronDeleteTool
            cron_storage = CronStorage(self._cwd / ".llmcode" / "scheduled_tasks.json")
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
                    swarm_dir=self._cwd / ".llmcode" / "swarm",
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
                # Create and register coordinator tool
                coordinator = Coordinator(
                    manager=swarm_mgr,
                    provider=self._runtime._provider if self._runtime else None,
                    config=self._config,
                )
                try:
                    self._tool_reg.register(CoordinatorTool(coordinator))
                except ValueError:
                    pass
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

            task_dir = self._cwd / ".llmcode" / "tasks"
            diag_dir = self._cwd / ".llmcode" / "diagnostics"
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
                self._lsp_manager = LspServerManager()
                for tool in (
                    LspGotoDefinitionTool(self._lsp_manager),
                    LspFindReferencesTool(self._lsp_manager),
                    LspDiagnosticsTool(self._lsp_manager),
                    LspHoverTool(self._lsp_manager),
                    LspDocumentSymbolTool(self._lsp_manager),
                    LspWorkspaceSymbolTool(self._lsp_manager),
                    LspImplementationTool(self._lsp_manager),
                    LspCallHierarchyTool(self._lsp_manager),
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

        # Initialize telemetry — pass the config straight through; both
        # `from llm_code.runtime.config import TelemetryConfig` and
        # `from llm_code.runtime.telemetry import TelemetryConfig` resolve
        # to the same class (see telemetry config consolidation, Plan 5.5).
        telemetry = None
        if getattr(self._config, "telemetry", None) and self._config.telemetry.enabled:
            try:
                from llm_code.runtime.telemetry import Telemetry
                telemetry = Telemetry(self._config.telemetry)
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
            typed_memory_store=self._typed_memory,
            task_manager=self._task_manager,
            project_index=self._project_index,
            lsp_manager=self._lsp_manager,
        )
        # Install MCP event sink so non-root server spawns surface an
        # inline approval widget.
        try:
            self._runtime.set_mcp_event_sink(self._on_mcp_approval_event)
        except Exception:
            pass

    def _on_mcp_approval_event(self, event) -> None:
        """Sink called by ConversationRuntime.request_mcp_approval.

        Mounts an MCPApprovalInline widget; resolution flows back through
        ``runtime.send_mcp_approval_response`` when the user presses y/a/n.
        """
        try:
            from llm_code.tui.chat_widgets import MCPApprovalInline
            chat = self.query_one(ChatScrollView)
            widget = MCPApprovalInline(
                server_name=event.server_name,
                owner_agent_id=event.owner_agent_id,
                command=event.command,
                description=event.description,
            )
            chat.add_entry(widget)
            self._mcp_approval_widget = widget
            self._mcp_approval_pending = True
        except Exception:
            logger.warning("failed to mount MCPApprovalInline", exc_info=True)

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

    def _paste_clipboard_image(self, *, silent: bool = False) -> None:
        """Try to capture an image from the system clipboard.

        Args:
            silent: If True, don't show error when no image is found.
                    Used by on_paste fallback; Ctrl+I sets silent=False.
        """
        chat = self.query_one(ChatScrollView)
        input_bar = self.query_one(InputBar)
        try:
            from llm_code.cli.image import capture_clipboard_image
            img = capture_clipboard_image()
            if img is not None:
                self._pending_images.append(img)
                input_bar.insert_image_marker()
            elif not silent:
                chat.add_entry(AssistantText("No image found in clipboard."))
        except (ImportError, FileNotFoundError, OSError):
            if not silent:
                chat.add_entry(AssistantText("Clipboard not available (install pngpaste: brew install pngpaste)."))
        except Exception as exc:
            if not silent:
                chat.add_entry(AssistantText(f"Clipboard error: {exc}"))

    def on_paste(self, event) -> None:
        """Handle terminal paste events — insert text and check for images.

        When user presses Cmd+V (macOS) or Ctrl+V (Linux), the terminal
        pastes text via bracket paste mode.  We insert the text into
        InputBar, and also check the clipboard for an image.
        """
        # Insert pasted text into InputBar
        paste_text = getattr(event, "text", "")
        if paste_text:
            input_bar = self.query_one(InputBar)
            if not input_bar.disabled:
                input_bar.value = (
                    input_bar.value[:input_bar._cursor]
                    + paste_text
                    + input_bar.value[input_bar._cursor:]
                )
                input_bar._cursor += len(paste_text)
                input_bar.refresh()
                return  # text paste — don't check for image
        # No text pasted — silently check clipboard for image
        self._paste_clipboard_image(silent=True)

    def on_screen_resume(self) -> None:
        """Return focus to InputBar after any modal screen closes."""
        self.query_one(InputBar).focus()

    def _on_idle(self) -> None:
        """Ensure InputBar stays focused during normal operation."""
        try:
            input_bar = self.query_one(InputBar)
            # Only refocus on the default screen (not during modals)
            if self.screen is self.screen_stack[0] and self.focused is not input_bar:
                if not input_bar.disabled:
                    input_bar.focus()
        except Exception:
            pass

    def on_input_bar_submitted(self, event: InputBar.Submitted) -> None:
        """Handle user input submission."""
        input_bar = self.query_one(InputBar)
        # Strip image markers from submitted value
        marker = InputBar._IMAGE_MARKER
        clean_text = event.value.replace(marker, "").strip()
        if not clean_text and not self._pending_images:
            return

        chat = self.query_one(ChatScrollView)
        chat.resume_auto_scroll()

        # Show user message with inline image markers rendered
        if self._pending_images:
            n = len(self._pending_images)
            label = f"{n} image{'s' if n > 1 else ''}"
            display = f"[{label}] {clean_text}" if clean_text else f"[{label}]"
            chat.add_entry(UserMessage(display))
        else:
            chat.add_entry(UserMessage(clean_text))
        text = clean_text

        if text.startswith("/"):
            self._handle_slash_command(text)
        else:
            # Pass pending images to runtime and reset
            images = list(self._pending_images)
            self._pending_images.clear()
            input_bar.pending_image_count = 0
            self.run_worker(self._run_turn(text, images=images), name="run_turn")

    def on_input_bar_cancelled(self, event: InputBar.Cancelled) -> None:
        """Handle Escape — cancel running generation."""
        pass  # Phase 2: cancel runtime

    def _refresh_rate_limit_bar(self) -> None:
        """Hide RateLimitBar when no rate-limit info; refresh otherwise."""
        try:
            from llm_code.tui.chat_widgets import RateLimitBar as _RLB
            bar = self.query_one(_RLB)
            info = getattr(self._cost_tracker, "rate_limit_info", None) if self._cost_tracker else None
            bar.display = info is not None
            bar.refresh()
        except Exception:
            pass

    def _insert_text_into_input(self, text: str) -> None:
        """Insert text at the InputBar cursor (used by Quick Open selection)."""
        try:
            self.query_one(InputBar).insert_text(text)
        except Exception:
            pass

    def _last_error_tool_block(self):
        """Return the most recently mounted ToolBlock with is_error=True, or None."""
        from llm_code.tui.chat_widgets import ToolBlock
        try:
            chat = self.query_one(ChatScrollView)
            blocks = list(chat.query(ToolBlock))
        except Exception:
            return None
        for blk in reversed(blocks):
            if getattr(blk._data, "is_error", False):
                return blk
        return None

    def _toggle_last_error_verbose(self) -> bool:
        """Toggle verbose on the last error ToolBlock. Returns True if a block was toggled."""
        blk = self._last_error_tool_block()
        if blk is None:
            return False
        blk.set_verbose(not blk._verbose)
        return True

    def _open_quick_open(self) -> None:
        """Mount QuickOpen modal screen. On selection, insert path into InputBar."""
        from textual.screen import ModalScreen
        from textual.containers import VerticalScroll
        from textual.widgets import Static
        from llm_code.tui.quick_open import fuzzy_find_files
        from rich.text import Text as RichText

        app_ref = self
        cwd = self._cwd

        class QuickOpenScreen(ModalScreen):
            DEFAULT_CSS = """
            QuickOpenScreen { align: center middle; }
            #qo-box {
                width: 80%;
                height: 60%;
                background: $surface;
                border: round $accent;
                padding: 1 2;
            }
            #qo-content { height: 1fr; }
            #qo-footer { dock: bottom; height: 1; color: $text-muted; text-align: center; }
            """

            def __init__(self) -> None:
                super().__init__()
                self._query = ""
                self._cursor = 0
                self._results: list = []

            def compose(self):
                with VerticalScroll(id="qo-box"):
                    yield Static("", id="qo-content")
                yield Static("type to filter · ↑↓ navigate · Enter select · Esc close", id="qo-footer")

            def on_mount(self) -> None:
                self._refresh()

            def _refresh(self) -> None:
                try:
                    self._results = fuzzy_find_files(self._query, cwd, limit=12)
                except Exception:
                    self._results = []
                if self._cursor >= len(self._results):
                    self._cursor = max(0, len(self._results) - 1)
                text = RichText()
                text.append(f"> {self._query}\n\n", style="bold cyan")
                if not self._results:
                    text.append("  (no matches)\n", style="dim")
                else:
                    for i, r in enumerate(self._results):
                        if i == self._cursor:
                            text.append("  > ", style="bold cyan")
                            text.append(f"{r.path}\n", style="bold white")
                        else:
                            text.append(f"    {r.path}\n", style="white")
                try:
                    self.query_one("#qo-content", Static).update(text)
                except Exception:
                    pass

            def on_key(self, event) -> None:
                key = event.key
                if key == "escape":
                    self.dismiss()
                elif key == "up":
                    self._cursor = max(0, self._cursor - 1)
                    self._refresh()
                elif key == "down":
                    if self._results:
                        self._cursor = min(len(self._results) - 1, self._cursor + 1)
                        self._refresh()
                elif key == "enter":
                    if 0 <= self._cursor < len(self._results):
                        path = self._results[self._cursor].path
                        self.dismiss()
                        app_ref._insert_text_into_input(path)
                elif key == "backspace":
                    self._query = self._query[:-1]
                    self._refresh()
                elif len(key) == 1 and key.isprintable():
                    self._query += key
                    self._refresh()
                else:
                    return
                event.prevent_default()
                event.stop()

        self.push_screen(QuickOpenScreen())

    def on_key(self, event: "events.Key") -> None:
        """Handle single-key permission responses (y/n/a), Ctrl+P quick open, Ctrl+V verbose toggle."""
        # Ctrl+P — Quick Open (does not conflict with paste; paste is on_paste / Ctrl+I)
        if event.key == "ctrl+p":
            self._open_quick_open()
            event.prevent_default()
            event.stop()
            return
        # Ctrl+V — toggle verbose on last error ToolBlock only (no-op otherwise, pastes unaffected)
        if event.key == "ctrl+v":
            if self._toggle_last_error_verbose():
                event.prevent_default()
                event.stop()
            return
        # MCP approval handling (y/a/n) — takes precedence over tool permission
        if self._mcp_approval_pending and self._runtime is not None:
            mcp_response_map = {
                "y": "allow",
                "a": "always",
                "n": "deny",
                "escape": "deny",
            }
            mcp_resp = mcp_response_map.get(event.key)
            if mcp_resp is not None:
                self._runtime.send_mcp_approval_response(mcp_resp)
                self._mcp_approval_pending = False
                if self._mcp_approval_widget is not None:
                    try:
                        w = self._mcp_approval_widget
                        self._mcp_approval_widget = None
                        if getattr(w, "is_mounted", False):
                            w.remove()
                    except Exception:
                        pass
                event.prevent_default()
                event.stop()
                return

        # Permission handling (y/n/a)
        if not self._permission_pending or self._runtime is None:
            return
        # y = allow once; a = always-by-kind/prefix; A = always-this-exact; n = deny
        # 'a' and 'A' are distinct keys (shift modifier).
        response_map = {
            "y": "allow",
            "n": "deny",
            "a": "always_kind",
            "A": "always_exact",
            "shift+a": "always_exact",
        }
        response = response_map.get(event.key)
        if response is not None:
            self._runtime.send_permission_response(response)
            event.prevent_default()
            event.stop()
            return
        if event.key == "e":
            # TODO: edit-args inline editor (Tier 2 follow-up)
            event.prevent_default()
            event.stop()

    async def _run_turn(self, user_input: str, images: list | None = None) -> None:
        """Run a conversation turn with full streaming event handling.

        If _active_skill is set, its content is injected into the system prompt.
        """
        import asyncio
        import time
        from llm_code.api.types import (
            StreamPermissionRequest, StreamTextDelta, StreamThinkingDelta,
            StreamToolExecStart, StreamToolExecResult, StreamToolProgress,
            StreamMessageStop, StreamCompactionStart, StreamCompactionDone,
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
        status.turn_count = int(getattr(status, "turn_count", 0) or 0) + 1

        # Reset per-turn counters
        turn_input_tokens = 0
        turn_output_tokens = 0
        self._context_warned = False
        # Per-turn map: tool_id → live ToolBlock (so Result events update
        # the same widget that was created at Start, no second mount)
        _pending_tools: dict[str, ToolBlock] = {}

        # Show skill router activations (run router here so user sees it
        # before the LLM call starts)
        _tui_cfg = getattr(self._config, "tui", None)
        _verb_override = tuple(getattr(_tui_cfg, "spinner_verbs", ()) or ())
        _verb_mode = getattr(_tui_cfg, "spinner_verbs_mode", "append") or "append"
        if self._runtime._skill_router is not None:
            _routing_spinner = SpinnerLine(
                verb_override=_verb_override, verb_mode=_verb_mode,
            )
            _routing_spinner.phase = "routing"
            chat.add_entry(_routing_spinner)
            try:
                _matched = await self._runtime._skill_router.route_async(user_input)
                if _matched:
                    chat.add_entry(SkillBadge([s.name for s in _matched]))
            except Exception:
                import logging
                logging.getLogger(__name__).warning(
                    "skill router failed", exc_info=True,
                )
            finally:
                try:
                    _routing_spinner.remove()
                except Exception:
                    pass

        spinner = SpinnerLine(
            verb_override=_verb_override, verb_mode=_verb_mode,
        )
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
        # Client-side tag parsing for models (like Qwen) that emit
        # <think> and <tool_call> as raw StreamTextDelta
        _in_think_tag = False
        _think_close_tag = "</think>"
        _in_tool_call_tag = False
        _saw_tool_call_this_turn = False  # For empty-response diagnosis
        _raw_text_buffer = ""
        _is_first_text_delta = True  # Track first delta for think-start detection
        _full_text_accumulator = ""  # Accumulate ALL text for post-hoc think stripping

        async def remove_spinner() -> None:
            """Remove spinner if it is currently mounted."""
            if spinner.is_mounted:
                await spinner.remove()

        perm_widget = None

        # Sync plan mode flag to runtime before each turn
        self._runtime.plan_mode = self._plan_mode

        try:
            # Consume active skill content (one-shot injection)
            _skill_content = None
            if hasattr(self, "_active_skill") and self._active_skill is not None:
                _skill_content = self._active_skill.content
                self._active_skill = None

            async for event in self._runtime.run_turn(
                user_input, images=images, active_skill_content=_skill_content,
            ):
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
                    _raw_text_buffer += event.text

                    # ── First delta detection ──
                    # Qwen (and similar) always start with <think> when thinking.
                    # On the first text delta, check if response starts with a think tag.
                    if _is_first_text_delta:
                        stripped_start = _raw_text_buffer.lstrip()
                        # Still accumulating a potential partial tag prefix
                        if len(stripped_start) < len("<thinking>") and stripped_start.startswith("<"):
                            continue  # wait for more data
                        _is_first_text_delta = False
                        for open_tag, close_tag in [("<think>", "</think>"), ("<thinking>", "</thinking>")]:
                            if stripped_start.startswith(open_tag):
                                # Strip the open tag and enter thinking mode
                                idx = _raw_text_buffer.index(open_tag) + len(open_tag)
                                _raw_text_buffer = _raw_text_buffer[idx:]
                                _in_think_tag = True
                                _think_close_tag = close_tag
                                spinner.phase = "thinking"
                                break

                    # ── Thinking mode: route everything to thinking_buffer ──
                    if _in_think_tag:
                        if _think_close_tag in _raw_text_buffer:
                            think_content, _, _raw_text_buffer = _raw_text_buffer.partition(_think_close_tag)
                            thinking_buffer += think_content
                            _in_think_tag = False
                            if thinking_buffer.strip():
                                elapsed_t = time.monotonic() - thinking_start
                                tokens_t = len(thinking_buffer) // 4
                                chat.add_entry(ThinkingBlock(thinking_buffer, elapsed_t, tokens_t))
                                thinking_buffer = ""
                            # After closing think, check for another think block
                            _is_first_text_delta = True
                        else:
                            thinking_buffer += _raw_text_buffer
                            _raw_text_buffer = ""
                        continue

                    # ── Mid-stream think tags (e.g. after tool results) ──
                    for open_tag, close_tag in [("<think>", "</think>"), ("<thinking>", "</thinking>")]:
                        if open_tag in _raw_text_buffer:
                            before, _, _raw_text_buffer = _raw_text_buffer.partition(open_tag)
                            if before.strip():
                                if not assistant_added:
                                    await remove_spinner()
                                    chat.add_entry(assistant)
                                    assistant_added = True
                                assistant.append_text(before)
                            _in_think_tag = True
                            _think_close_tag = close_tag
                            spinner.phase = "thinking"
                            # Re-process remaining buffer in thinking mode
                            if _think_close_tag in _raw_text_buffer:
                                tc, _, _raw_text_buffer = _raw_text_buffer.partition(_think_close_tag)
                                thinking_buffer += tc
                                _in_think_tag = False
                                if thinking_buffer.strip():
                                    elapsed_t = time.monotonic() - thinking_start
                                    tokens_t = len(thinking_buffer) // 4
                                    chat.add_entry(ThinkingBlock(thinking_buffer, elapsed_t, tokens_t))
                                    thinking_buffer = ""
                            else:
                                thinking_buffer += _raw_text_buffer
                                _raw_text_buffer = ""
                            continue

                    # ── Handle <tool_call> tags ──
                    while "<tool_call>" in _raw_text_buffer and not _in_tool_call_tag:
                        before, _, _raw_text_buffer = _raw_text_buffer.partition("<tool_call>")
                        if before.strip():
                            if not assistant_added:
                                await remove_spinner()
                                chat.add_entry(assistant)
                                assistant_added = True
                            assistant.append_text(before)
                        _in_tool_call_tag = True
                        _saw_tool_call_this_turn = True

                    if _in_tool_call_tag:
                        if "</tool_call>" in _raw_text_buffer:
                            _, _, _raw_text_buffer = _raw_text_buffer.partition("</tool_call>")
                            _in_tool_call_tag = False
                        else:
                            _raw_text_buffer = ""
                        continue

                    # ── Safety: strip any remaining think tags ──
                    for _tag in ("<think>", "</think>", "<thinking>", "</thinking>"):
                        _raw_text_buffer = _raw_text_buffer.replace(_tag, "")

                    # ── Normal text — output to assistant ──
                    if _raw_text_buffer:
                        # Hold back potential partial tags
                        last_lt = _raw_text_buffer.rfind("<")
                        if last_lt >= 0 and ">" not in _raw_text_buffer[last_lt:]:
                            flush = _raw_text_buffer[:last_lt]
                            _raw_text_buffer = _raw_text_buffer[last_lt:]
                        else:
                            flush = _raw_text_buffer
                            _raw_text_buffer = ""
                        if flush:
                            if not assistant_added:
                                await remove_spinner()
                                chat.add_entry(assistant)
                                assistant_added = True
                            assistant.append_text(flush)
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
                    # Track by tool_id so the matching Result event updates
                    # this widget in place instead of mounting a second one.
                    if event.tool_id:
                        _pending_tools[event.tool_id] = tool_widget
                    spinner.phase = "running"
                    spinner._tool_name = event.tool_name
                    chat.add_entry(spinner)

                elif isinstance(event, StreamToolExecResult):
                    await remove_spinner()
                    existing = _pending_tools.pop(event.tool_id, None) if event.tool_id else None
                    if existing is not None:
                        # Update the running widget in place — no second block
                        existing.update_result(
                            event.output[:200], event.is_error,
                        )
                    else:
                        # Fallback: no matching start (shouldn't happen with
                        # paired emit, but handle gracefully)
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
                        event.tool_name,
                        event.args_preview,
                        diff_lines=event.diff_lines,
                        pending_files=event.pending_files,
                    )
                    chat.add_entry(perm_widget)
                    self._permission_pending = True
                    # No explicit wait — the runtime generator is suspended
                    # on its own asyncio.Future. The async for loop blocks on
                    # __anext__ until y/n/a resolves the Future via on_key →
                    # send_permission_response. Cleanup at top of loop.

                elif isinstance(event, StreamCompactionStart):
                    spinner.phase = "compacting"
                    try:
                        chat.add_entry(AssistantText(
                            f"[auto-compacting: {event.used_tokens}/{event.max_tokens} tokens]"
                        ))
                    except Exception:
                        pass

                elif isinstance(event, StreamCompactionDone):
                    try:
                        chat.add_entry(AssistantText(
                            f"[compacted: {event.before_messages} → {event.after_messages} messages]"
                        ))
                    except Exception:
                        pass

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
                        # Real-time status bar update
                        status.tokens = self._output_tokens
                        if self._cost_tracker:
                            cost_usd = self._cost_tracker.total_cost_usd
                            status.cost = f"${cost_usd:.4f}" if cost_usd > 0.0001 else ""
                        # Context window meter: input tokens approximate
                        # current context fill (input is the full re-sent state).
                        status.context_used = event.usage.input_tokens
                        if (
                            not self._context_warned
                            and status.context_limit > 0
                            and status.context_pct() >= 80.0
                        ):
                            self._context_warned = True
                            chat.add_entry(AssistantText(
                                "⚠ Context window is "
                                f"{int(status.context_pct())}% full. "
                                "Run /compact to summarize older messages "
                                "and free space."
                            ))

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

        # Flush any remaining buffers after stream ends
        if thinking_buffer.strip():
            elapsed_t = time.monotonic() - thinking_start
            tokens_t = len(thinking_buffer) // 4
            chat.add_entry(ThinkingBlock(thinking_buffer, elapsed_t, tokens_t))

        if _raw_text_buffer.strip():
            if not assistant_added:
                chat.add_entry(assistant)
                assistant_added = True
            assistant.append_text(_raw_text_buffer)
            _raw_text_buffer = ""

        # If no text was ever displayed but we DO have thinking content,
        # surface the thinking as the answer. Reasoning models (Qwen3,
        # DeepSeek-R1) sometimes emit the entire useful response inside
        # the <think> block and never produce a "final" text token,
        # especially on short prompts where they reason themselves into
        # silence. Better to show the reasoning than a cryptic error.
        if not assistant_added and thinking_buffer.strip():
            chat.add_entry(AssistantText(thinking_buffer.strip()))
            assistant_added = True
        elif not assistant_added and turn_output_tokens > 0:
            # Distinguish why visible output is empty. The most common cause
            # (now that thinking parsing is robust) is that the model emitted
            # only a <tool_call> XML block — which gets stripped from the
            # visible stream — for a query that doesn't actually need a tool.
            # Pick message language to match the user's input language.
            chat.add_entry(AssistantText(
                _empty_response_message(
                    saw_tool_call=_saw_tool_call_this_turn,
                    user_input=user_input,
                    session_messages=getattr(self._runtime, "session", None) and self._runtime.session.messages,
                )
            ))

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
            return

        # Check user-defined custom commands (project + global)
        try:
            from llm_code.runtime.custom_commands import discover_custom_commands
            custom = discover_custom_commands(self._cwd)
            if name in custom:
                cmd_def = custom[name]
                rendered = cmd_def.render(args)
                chat = self.query_one(ChatScrollView)
                chat.add_entry(AssistantText(f"Running custom command: /{name}"))
                images = list(self._pending_images)
                self._pending_images.clear()
                self.query_one(InputBar).pending_image_count = 0
                self.run_worker(self._run_turn(rendered, images=images), name="run_turn")
                return
        except Exception:
            pass

        # Check loaded skills — superpowers etc. register as command skills
        if self._skills:
            for skill in self._skills.command_skills:
                if skill.trigger == name:
                    # Activate the skill: inject its content as context for the next turn
                    chat = self.query_one(ChatScrollView)
                    chat.add_entry(AssistantText(f"Activated skill: {skill.name}"))
                    # Run as a turn with the skill content as system context
                    prompt = args if args else f"Using skill: {skill.name}"
                    self._active_skill = skill
                    images = list(self._pending_images)
                    self._pending_images.clear()
                    self.query_one(InputBar).pending_image_count = 0
                    self.run_worker(self._run_turn(prompt, images=images), name="run_turn")
                    return

        chat = self.query_one(ChatScrollView)
        # Suggest closest matching command or skill
        from difflib import get_close_matches
        from llm_code.cli.commands import KNOWN_COMMANDS
        all_names = set(KNOWN_COMMANDS)
        if self._skills:
            all_names.update(s.trigger for s in self._skills.command_skills if s.trigger)
        matches = get_close_matches(name, all_names, n=1, cutoff=0.5)
        if matches:
            chat.add_entry(AssistantText(
                f"Unknown command: /{name} — did you mean /{matches[0]}?"
            ))
        else:
            chat.add_entry(AssistantText(
                f"Unknown command: /{name} — type /help for help"
            ))

    def _cmd_compact(self, args: str) -> None:
        """Manually compact the conversation, freeing context window space."""
        chat = self.query_one(ChatScrollView)
        if self._runtime is None:
            chat.add_entry(AssistantText("Compaction unavailable: runtime not initialized."))
            return
        try:
            from llm_code.runtime.compaction import compact_session

            before_msgs = len(self._runtime.session.messages)
            before_toks = self._runtime.session.estimated_tokens()
            keep = 4
            try:
                keep = int(args.strip()) if args.strip() else 4
            except ValueError:
                keep = 4
            self._runtime.session = compact_session(
                self._runtime.session, keep_recent=keep, summary="(manual /compact)",
            )
            after_msgs = len(self._runtime.session.messages)
            after_toks = self._runtime.session.estimated_tokens()
            chat.add_entry(AssistantText(
                f"✓ Compacted: {before_msgs} → {after_msgs} messages, "
                f"~{before_toks:,} → ~{after_toks:,} tokens. "
                "Older messages summarized."
            ))
            try:
                status = self.query_one(StatusBar)
                status.context_used = after_toks
                self._context_warned = False
            except Exception:
                pass
        except Exception as exc:
            chat.add_entry(AssistantText(f"Compaction failed: {exc}"))

    def _cmd_exit(self, args: str) -> None:
        self.run_worker(self._graceful_exit(), name="graceful_exit")

    _cmd_quit = _cmd_exit

    def action_quit_app(self) -> None:
        """Textual action bound to Ctrl+D."""
        self.run_worker(self._graceful_exit(), name="graceful_exit")

    def _poll_bg_tasks(self) -> None:
        """Periodic update of background task count in status bar.

        Combines lifecycle task count (PLAN/DO/VERIFY) with the in-flight
        asyncio task registry (background read-tool execution, etc.).
        """
        count = 0
        if self._task_manager is not None:
            try:
                count += self._task_manager.running_task_count()
            except Exception:
                pass
        try:
            from llm_code.runtime.background_task_registry import (
                global_async_registry,
            )

            count += global_async_registry().active_count()
        except Exception:
            pass
        try:
            self.query_one(StatusBar).bg_tasks = count
        except Exception:
            pass

    def action_cycle_agent(self) -> None:
        """Cycle through primary agents (normal → plan → suggest → normal).

        Equivalent to opencode's Tab agent switching between build/plan modes.
        Each agent has different permission semantics:
          - normal: workspace_write, all tools available
          - plan: read-only, planning before execution
          - suggest: prompts for every elevated tool
        """
        from llm_code.runtime.permissions import PermissionMode

        if self._runtime is None or self._runtime._permissions is None:
            return

        chat = self.query_one(ChatScrollView)
        status = self.query_one(StatusBar)
        policy = self._runtime._permissions

        # Cycle order: normal → plan → suggest → normal
        cycle = [
            (PermissionMode.WORKSPACE_WRITE, "", "BUILD"),
            (PermissionMode.PLAN, "PLAN", "PLAN"),
            (PermissionMode.PROMPT, "SUGGEST", "SUGGEST"),
        ]
        current_mode = getattr(policy, "_mode", PermissionMode.WORKSPACE_WRITE)
        current_idx = next(
            (i for i, (m, _, _) in enumerate(cycle) if m == current_mode), 0
        )
        next_idx = (current_idx + 1) % len(cycle)
        next_mode, status_label, agent_name = cycle[next_idx]
        policy._mode = next_mode
        status.plan_mode = status_label
        # Map cycle label to permission_mode reactive for status bar
        _perm_label_map = {
            "BUILD": "build",
            "PLAN": "plan",
            "SUGGEST": "suggest",
        }
        status.permission_mode = _perm_label_map.get(agent_name, "")
        chat.add_entry(AssistantText(f"Agent: {agent_name}"))

    def action_scroll_chat_up(self) -> None:
        """Scroll chat view up by one page."""
        chat = self.query_one(ChatScrollView)
        chat.scroll_page_up(animate=False)
        chat.pause_auto_scroll()

    def action_scroll_chat_down(self) -> None:
        """Scroll chat view down by one page."""
        chat = self.query_one(ChatScrollView)
        chat.scroll_page_down(animate=False)
        chat.resume_auto_scroll()

    async def _graceful_exit(self) -> None:
        """Dream consolidation + cancel background tasks + exit."""
        import asyncio as _aio
        try:
            await _aio.wait_for(self._dream_on_exit(), timeout=5.0)
        except Exception:
            pass
        # Cancel any in-flight background asyncio tasks within a short budget.
        try:
            from llm_code.runtime.background_task_registry import (
                global_async_registry,
            )

            await global_async_registry().cancel_all(timeout=2.0)
        except Exception:
            pass
        self.exit()

    async def _dream_on_exit(self) -> None:
        """Fire DreamTask consolidation + knowledge compilation on session exit."""
        import asyncio as _aio
        if not self._memory or not self._runtime:
            return

        dream_summary = ""
        try:
            from llm_code.runtime.dream import DreamTask
            dream = DreamTask()
            dream_summary = await _aio.wait_for(
                dream.consolidate(
                    self._runtime.session,
                    self._memory,
                    self._runtime._provider,
                    self._config,
                ),
                timeout=30.0,
            )
        except Exception:
            pass

        # Knowledge compilation (after DreamTask, best-effort)
        if getattr(self._config, "knowledge", None) and self._config.knowledge.compile_on_exit:
            try:
                from llm_code.runtime.knowledge_compiler import KnowledgeCompiler
                compile_model = self._config.knowledge.compile_model or getattr(
                    self._config.model_routing, "compaction", ""
                )
                compiler = KnowledgeCompiler(
                    cwd=self._cwd,
                    llm_provider=self._runtime._provider,
                    compile_model=compile_model,
                )
                facts = []
                if dream_summary:
                    for line in dream_summary.splitlines():
                        stripped = line.strip()
                        if stripped.startswith("- ") and not stripped.startswith("- ["):
                            facts.append(stripped[2:])
                ingest_data = compiler.ingest(facts=facts, since_commit=None)
                await _aio.wait_for(compiler.compile(ingest_data), timeout=30.0)
            except Exception:
                pass

    def _cmd_help(self, args: str) -> None:
        from textual.screen import ModalScreen
        from textual.containers import VerticalScroll
        from textual.widgets import Static
        from rich.text import Text as RichText

        skills = self._skills
        app_ref = self

        from llm_code.cli.commands import COMMAND_REGISTRY

        _COMMANDS = [
            (f"/{c.name}", c.description)
            for c in COMMAND_REGISTRY
            if c.name not in ("quit",)  # skip duplicate of /exit
        ]

        _custom_cmds: list[tuple[str, str]] = []
        if skills:
            for s in sorted(
                list(skills.auto_skills) + list(skills.command_skills),
                key=lambda x: x.name,
            ):
                trigger = f"/{s.trigger}" if s.trigger else f"(auto: {s.name})"
                desc = s.description if hasattr(s, "description") and s.description else s.name
                source = "user" if not getattr(s, "plugin", None) else f"({s.plugin})"
                _custom_cmds.append((trigger, f"{desc} {source}"))

        class HelpScreen(ModalScreen):
            DEFAULT_CSS = """
            HelpScreen { align: center middle; }
            #help-box {
                width: 90%;
                height: 85%;
                background: $surface;
                border: round $accent;
                padding: 1 2;
            }
            #help-content { height: 1fr; }
            #help-footer {
                dock: bottom;
                height: 1;
                color: $text-muted;
                text-align: center;
            }
            """

            def __init__(self) -> None:
                super().__init__()
                self._tab = 0
                self._cursor = 0
                self._tab_names = ["general", "commands", "custom-commands"]

            def compose(self):
                with VerticalScroll(id="help-box"):
                    yield Static("Loading...", id="help-content")
                yield Static("← → tabs · ↑↓ navigate · Enter execute · Esc close", id="help-footer")

            def on_mount(self):
                self._refresh_content()

            def on_key(self, event) -> None:
                key = event.key
                if key == "escape":
                    self.dismiss()
                elif key == "left":
                    self._tab = max(0, self._tab - 1)
                    self._cursor = 0
                    self._refresh_content()
                elif key == "right":
                    self._tab = min(2, self._tab + 1)
                    self._cursor = 0
                    self._refresh_content()
                elif key == "up" and self._tab > 0:
                    self._cursor = max(0, self._cursor - 1)
                    self._refresh_content()
                elif key == "down" and self._tab > 0:
                    items = _COMMANDS if self._tab == 1 else _custom_cmds
                    self._cursor = min(len(items) - 1, self._cursor + 1)
                    self._refresh_content()
                elif key == "enter" and self._tab > 0:
                    items = _COMMANDS if self._tab == 1 else _custom_cmds
                    if 0 <= self._cursor < len(items):
                        cmd = items[self._cursor][0]
                        self.dismiss()
                        # Execute the command after dismiss
                        app_ref.query_one(InputBar).value = ""
                        app_ref._handle_slash_command(cmd)
                event.prevent_default()
                event.stop()

            def _render_header(self) -> RichText:
                text = RichText()
                text.append("llm-code", style="bold cyan")
                text.append("  ", style="dim")
                for i, name in enumerate(self._tab_names):
                    if i == self._tab:
                        text.append(f" {name} ", style="bold white on #3a3a5a")
                    else:
                        text.append(f"  {name}  ", style="dim")
                text.append("\n\n")
                return text

            def _refresh_content(self) -> None:
                content = self.query_one("#help-content", Static)
                from rich.console import Console
                from io import StringIO
                if self._tab == 0:
                    rt = self._build_general()
                elif self._tab == 1:
                    rt = self._build_list("Browse default commands:", _COMMANDS)
                else:
                    rt = self._build_list("Browse custom commands:", _custom_cmds)
                # Render Rich Text to ANSI string for Static.update()
                buf = StringIO()
                console = Console(file=buf, force_terminal=True, width=120)
                console.print(rt, end="")
                content.update(buf.getvalue())

            def _build_general(self) -> RichText:
                text = self._render_header()
                text.append(
                    "llm-code understands your codebase, makes edits with your "
                    "permission, and executes commands — right from your terminal.\n\n",
                    style="white",
                )
                text.append("Shortcuts\n", style="bold white")
                shortcuts = [
                    ("! for bash mode", "double tap esc to clear", "Ctrl+D to quit"),
                    ("/ for commands", "Shift+Enter for multiline", "Ctrl+I to paste images"),
                    ("/skill browse skills", "Page Up/Down to scroll", "/vim toggle vim"),
                    ("/plugin browse plugins", "Tab to autocomplete", "/model switch model"),
                    ("/mcp MCP servers", "Ctrl+O verbose output", "/undo revert changes"),
                ]
                for row in shortcuts:
                    for i, col in enumerate(row):
                        text.append(f"{col:<32s}", style="white" if i == 0 else "dim")
                    text.append("\n")
                return text

            def _build_list(self, title: str, items: list[tuple[str, str]]) -> RichText:
                text = self._render_header()
                text.append(f"{title}\n\n", style="white")
                if not items:
                    text.append("  No items available.\n", style="dim")
                    text.append("  Use /skill to browse and install.\n", style="dim")
                    return text
                for i, (cmd, desc) in enumerate(items):
                    if i == self._cursor:
                        text.append("  > ", style="bold cyan")
                        text.append(f"{cmd}\n", style="bold white")
                    else:
                        text.append(f"    {cmd}\n", style="bold white")
                    text.append(f"      {desc}\n", style="dim")
                return text

        self.push_screen(HelpScreen())

    def _cmd_copy(self, args: str) -> None:
        """Copy last assistant response to system clipboard."""
        chat = self.query_one(ChatScrollView)
        # Walk children in reverse to find last AssistantText
        for child in reversed(list(chat.children)):
            if isinstance(child, AssistantText):
                text = child._text
                if text:
                    self.copy_to_clipboard(text)
                    chat.add_entry(AssistantText("Copied to clipboard."))
                    return
        chat.add_entry(AssistantText("No response to copy."))

    def _cmd_clear(self, args: str) -> None:
        self.query_one(ChatScrollView).remove_children()

    def _cmd_model(self, args: str) -> None:
        chat = self.query_one(ChatScrollView)
        if args.strip() == "route":
            self._show_model_routes()
            return
        if args:
            import dataclasses
            self._config = dataclasses.replace(self._config, model=args)
            self._init_runtime()
            self.query_one(HeaderBar).model = args
            chat.add_entry(AssistantText(f"Model switched to: {args}"))
        else:
            model = self._config.model if self._config else "(not set)"
            chat.add_entry(AssistantText(f"Current model: {model}"))

    def _show_model_routes(self) -> None:
        """Display configured model routing table."""
        chat = self.query_one(ChatScrollView)
        routes: list[str] = []
        cfg = self._config
        if hasattr(cfg, "model") and cfg.model:
            routes.append(f"  {'default':<12s}  {cfg.model}")
        if hasattr(cfg, "model_routing") and cfg.model_routing:
            mr = cfg.model_routing
            for attr in ("sub_agent", "compaction", "fallback"):
                model = getattr(mr, attr, None)
                if model:
                    routes.append(f"  {attr:<12s}  {model}")
        if routes:
            chat.add_entry(AssistantText("Model routing:\n" + "\n".join(routes)))
        else:
            chat.add_entry(AssistantText("No model routing configured"))

    def _cmd_cost(self, args: str) -> None:
        cost = self._cost_tracker.format_cost() if self._cost_tracker else "No cost data"
        self.query_one(ChatScrollView).add_entry(AssistantText(cost))

    def _cmd_profile(self, args: str) -> None:
        """Show per-model token/cost breakdown from the query profiler."""
        chat = self.query_one(ChatScrollView)
        profiler = getattr(self._runtime, "_query_profiler", None) if self._runtime else None
        if profiler is None:
            chat.add_entry(AssistantText("(profiler not initialized)"))
            return
        pricing = getattr(self._config, "pricing", None)
        chat.add_entry(AssistantText(profiler.format_breakdown(pricing)))

    def _cmd_gain(self, args: str) -> None:
        from llm_code.tools.token_tracker import TokenTracker
        days = int(args) if args.strip().isdigit() else 30
        tracker = TokenTracker()
        report = tracker.format_report(days)
        tracker.close()
        self.query_one(ChatScrollView).add_entry(AssistantText(report))

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
            steps = 1
            if args.strip().isdigit():
                steps = int(args.strip())
            cp = self._checkpoint_mgr.undo(steps)
            if cp:
                label = f"Undone {steps} step(s)" if steps > 1 else "Undone"
                chat.add_entry(AssistantText(f"{label}: {cp.tool_name} ({cp.tool_args_summary[:50]})"))
        else:
            chat.add_entry(AssistantText("Nothing to undo."))

    def _cmd_diff(self, args: str) -> None:
        """Show diff since last checkpoint."""
        chat = self.query_one(ChatScrollView)
        if not self._checkpoint_mgr or not self._checkpoint_mgr.can_undo():
            chat.add_entry(AssistantText("No checkpoints available."))
            return
        last_cp = self._checkpoint_mgr.list_checkpoints()[-1]
        import subprocess
        result = subprocess.run(
            ["git", "diff", last_cp.git_sha, "HEAD"],
            capture_output=True, text=True, cwd=self._cwd,
        )
        if result.stdout.strip():
            chat.add_entry(AssistantText(f"```diff\n{result.stdout}\n```"))
        else:
            chat.add_entry(AssistantText("No changes since last checkpoint."))

    def _cmd_init(self, args: str) -> None:
        """Run an LLM-driven analysis of the repo to generate AGENTS.md."""
        from pathlib import Path as _Path
        chat = self.query_one(ChatScrollView)
        template_path = _Path(__file__).parent.parent / "cli" / "templates" / "init.md"
        if not template_path.is_file():
            chat.add_entry(AssistantText(f"Init template not found: {template_path}"))
            return
        try:
            template = template_path.read_text(encoding="utf-8")
        except OSError as exc:
            chat.add_entry(AssistantText(f"Failed to read init template: {exc}"))
            return
        prompt = template.replace("$ARGUMENTS", args.strip() or "(none)")
        chat.add_entry(AssistantText("Analyzing repo and generating AGENTS.md..."))
        images = list(self._pending_images)
        self._pending_images.clear()
        self.query_one(InputBar).pending_image_count = 0
        self.run_worker(self._run_turn(prompt, images=images), name="run_turn")

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
        input_bar = self.query_one(InputBar)
        if not args:
            chat.add_entry(AssistantText("Usage: /image <path>"))
            return
        try:
            from llm_code.cli.image import load_image_from_path
            img_path = Path(args).expanduser().resolve()
            img = load_image_from_path(str(img_path))
            self._pending_images.append(img)
            input_bar.insert_image_marker()
        except FileNotFoundError:
            chat.add_entry(AssistantText(f"Image not found: {args}"))

    def _cmd_lsp(self, args: str) -> None:
        self.query_one(ChatScrollView).add_entry(AssistantText("LSP: not started in this session."))

    def _cmd_cancel(self, args: str) -> None:
        if self._runtime and hasattr(self._runtime, '_cancel'):
            self._runtime._cancel()
        self.query_one(ChatScrollView).add_entry(AssistantText("(cancelled)"))

    def _cmd_plan(self, args: str) -> None:
        """Toggle plan/act mode."""
        self._plan_mode = not self._plan_mode
        status = self.query_one(StatusBar)
        chat = self.query_one(ChatScrollView)
        if self._plan_mode:
            status.plan_mode = "PLAN"
            chat.add_entry(AssistantText(
                "Plan mode ON -- agent will explore and plan without making changes."
            ))
        else:
            status.plan_mode = ""
            chat.add_entry(AssistantText(
                "Plan mode OFF -- back to normal."
            ))
        if self._runtime:
            self._runtime.plan_mode = self._plan_mode

    def _cmd_yolo(self, args: str) -> None:
        """Toggle YOLO mode — auto-accept all permission prompts.

        Equivalent to --dangerously-skip-permissions in Claude Code.
        """
        from llm_code.runtime.permissions import PermissionMode

        chat = self.query_one(ChatScrollView)
        status = self.query_one(StatusBar)

        if self._runtime is None or self._runtime._permissions is None:
            chat.add_entry(AssistantText("Runtime not initialized."))
            return

        policy = self._runtime._permissions
        # Toggle: if already in AUTO_ACCEPT, switch to PROMPT; otherwise enable YOLO
        current_mode = getattr(policy, "_mode", PermissionMode.PROMPT)
        if current_mode == PermissionMode.AUTO_ACCEPT:
            policy._mode = PermissionMode.PROMPT
            status.plan_mode = ""
            chat.add_entry(AssistantText(
                "YOLO mode OFF — permissions will prompt again."
            ))
        else:
            policy._mode = PermissionMode.AUTO_ACCEPT
            status.plan_mode = "YOLO"
            chat.add_entry(AssistantText(
                "YOLO mode ON — all permissions auto-accepted. "
                "⚠️  Be careful: write/delete operations will execute without confirmation."
            ))

    def _cmd_mode(self, args: str) -> None:
        """Switch between suggest/normal/plan modes."""
        from llm_code.runtime.permissions import PermissionMode

        chat = self.query_one(ChatScrollView)
        status = self.query_one(StatusBar)

        # Map mode names to PermissionMode values and status bar labels
        valid_modes = {
            "suggest": (PermissionMode.PROMPT, "SUGGEST"),
            "normal": (PermissionMode.WORKSPACE_WRITE, ""),
            "plan": (PermissionMode.PLAN, "PLAN"),
        }

        if not args.strip():
            # Determine current mode name from status bar state and plan flag
            if self._plan_mode:
                current = "plan"
            elif status.plan_mode == "SUGGEST":
                current = "suggest"
            else:
                current = "normal"
            chat.add_entry(AssistantText(
                f"Current mode: {current}\nAvailable: suggest, normal, plan"
            ))
            return

        mode_name = args.strip().lower()
        if mode_name not in valid_modes:
            chat.add_entry(AssistantText(
                f"Unknown mode: {mode_name}. Use: suggest, normal, plan"
            ))
            return

        perm_mode, label = valid_modes[mode_name]

        # Update plan mode flag
        self._plan_mode = mode_name == "plan"

        # Update status bar
        status.plan_mode = label

        # Update runtime permission policy mode
        if self._runtime and hasattr(self._runtime, "_permissions"):
            self._runtime._permissions._mode = perm_mode
        if self._runtime:
            self._runtime.plan_mode = self._plan_mode

        chat.add_entry(AssistantText(f"Switched to {mode_name} mode"))

    def _cmd_harness(self, args: str) -> None:
        """Show or configure harness controls."""
        chat = self.query_one(ChatScrollView)

        if not self._runtime or not hasattr(self._runtime, "_harness"):
            chat.add_entry(AssistantText("Harness not available."))
            return

        harness = self._runtime._harness
        parts = args.strip().split()

        if not parts:
            # Show status
            status = harness.status()
            lines = [f"Harness: {status['template']}\n"]
            lines.append("  Guides (feedforward):")
            for g in status["guides"]:
                mark = "✓" if g["enabled"] else "✗"
                lines.append(f"    {mark} {g['name']:<22} {g['trigger']:<12} {g['kind']}")
            lines.append("\n  Sensors (feedback):")
            for s in status["sensors"]:
                mark = "✓" if s["enabled"] else "✗"
                lines.append(f"    {mark} {s['name']:<22} {s['trigger']:<12} {s['kind']}")
            chat.add_entry(AssistantText("\n".join(lines)))
            return

        action = parts[0]
        if action == "enable" and len(parts) > 1:
            harness.enable(parts[1])
            chat.add_entry(AssistantText(f"Enabled: {parts[1]}"))
        elif action == "disable" and len(parts) > 1:
            harness.disable(parts[1])
            chat.add_entry(AssistantText(f"Disabled: {parts[1]}"))
        elif action == "template" and len(parts) > 1:
            from llm_code.harness.templates import default_controls
            from llm_code.harness.config import HarnessConfig
            new_controls = default_controls(parts[1])
            harness._config = HarnessConfig(template=parts[1], controls=new_controls)
            harness._overrides.clear()
            chat.add_entry(AssistantText(f"Switched to template: {parts[1]}"))
        else:
            chat.add_entry(AssistantText(
                "Usage: /harness [enable|disable|template] [name]\n"
                "  /harness              — show status\n"
                "  /harness enable X     — enable control X\n"
                "  /harness disable X    — disable control X\n"
                "  /harness template Y   — switch to template Y"
            ))

    def _cmd_knowledge(self, args: str) -> None:
        """View or rebuild the project knowledge base."""
        chat = self.query_one(ChatScrollView)

        parts = args.strip().split()
        action = parts[0] if parts else ""

        if action == "rebuild":
            import asyncio
            asyncio.ensure_future(self._rebuild_knowledge())
            return

        # Show knowledge index
        try:
            from llm_code.runtime.knowledge_compiler import KnowledgeCompiler
            compiler = KnowledgeCompiler(cwd=self._cwd, llm_provider=None)
            entries = compiler.get_index()
        except Exception:
            chat.add_entry(AssistantText("Knowledge base not available."))
            return

        if not entries:
            chat.add_entry(AssistantText(
                "Knowledge base is empty.\n"
                "It will be built automatically after your next session, "
                "or run `/knowledge rebuild` to build now."
            ))
            return

        lines = ["## Project Knowledge Base\n"]
        for entry in entries:
            lines.append(f"- **{entry.title}** — {entry.summary}")
        lines.append(f"\n{len(entries)} articles. Use `/knowledge rebuild` to force recompilation.")
        chat.add_entry(AssistantText("\n".join(lines)))

    async def _rebuild_knowledge(self) -> None:
        """Force full knowledge rebuild."""
        chat = self.query_one(ChatScrollView)
        if not self._runtime:
            chat.add_entry(AssistantText("Runtime not available."))
            return

        chat.add_entry(AssistantText("Rebuilding knowledge base..."))
        try:
            from llm_code.runtime.knowledge_compiler import KnowledgeCompiler
            compile_model = ""
            if hasattr(self._config, "knowledge"):
                compile_model = self._config.knowledge.compile_model
            if not compile_model and hasattr(self._config, "model_routing"):
                compile_model = self._config.model_routing.compaction
            compiler = KnowledgeCompiler(
                cwd=self._cwd,
                llm_provider=self._runtime._provider,
                compile_model=compile_model,
            )
            ingest_data = compiler.ingest(facts=[], since_commit=None)
            import asyncio
            await asyncio.wait_for(compiler.compile(ingest_data), timeout=60.0)
            entries = compiler.get_index()
            chat.add_entry(AssistantText(f"Knowledge base rebuilt: {len(entries)} articles."))
        except Exception as exc:
            chat.add_entry(AssistantText(f"Rebuild failed: {exc}"))

    def _cmd_dump(self, args: str) -> None:
        """Dump codebase for external LLM use (DAFC pattern)."""
        import asyncio
        asyncio.ensure_future(self._run_dump(args))

    async def _run_dump(self, args: str) -> None:
        from llm_code.tools.dump import dump_codebase
        chat = self.query_one(ChatScrollView)

        max_files = 200
        if args.strip().isdigit():
            max_files = int(args.strip())

        result = dump_codebase(self._cwd, max_files=max_files)

        if result.file_count == 0:
            chat.add_entry(AssistantText("No source files found to dump."))
            return

        # Write to file
        dump_path = self._cwd / ".llmcode" / "dump.txt"
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        dump_path.write_text(result.text, encoding="utf-8")

        chat.add_entry(AssistantText(
            f"Dumped {result.file_count} files "
            f"({result.total_lines:,} lines, ~{result.estimated_tokens:,} tokens)\n"
            f"Saved to: {dump_path}"
        ))

    def _cmd_analyze(self, args: str) -> None:
        """Run code analysis rules on the codebase."""
        import asyncio
        asyncio.ensure_future(self._run_analyze(args))

    async def _run_analyze(self, args: str) -> None:
        from llm_code.analysis.engine import run_analysis
        chat = self.query_one(ChatScrollView)

        target = Path(args.strip()) if args.strip() else self._cwd
        if not target.is_absolute():
            target = self._cwd / target

        try:
            result = run_analysis(target)
        except Exception as exc:
            chat.add_entry(AssistantText(f"Analysis failed: {exc}"))
            return

        chat.add_entry(AssistantText(result.format_chat()))

        # Store context for injection into future prompts
        if result.violations:
            self._analysis_context = result.format_context(max_tokens=1000)
            if self._runtime is not None:
                self._runtime.analysis_context = self._analysis_context
        else:
            self._analysis_context = None
            if self._runtime is not None:
                self._runtime.analysis_context = None

    def _cmd_diff_check(self, args: str) -> None:
        """Show new and fixed violations compared with cached results."""
        import asyncio
        asyncio.ensure_future(self._run_diff_check(args))

    async def _run_diff_check(self, args: str) -> None:
        from llm_code.analysis.engine import run_diff_check
        chat = self.query_one(ChatScrollView)

        try:
            new_violations, fixed_violations = run_diff_check(self._cwd)
        except Exception as exc:
            chat.add_entry(AssistantText(f"Diff check failed: {exc}"))
            return

        if not new_violations and not fixed_violations:
            chat.add_entry(AssistantText("No changes in violations since last analysis."))
            return

        lines: list[str] = ["## Diff Check"]
        for v in new_violations:
            loc = f"{v.file_path}:{v.line}" if v.line > 0 else v.file_path
            lines.append(f"NEW {v.severity.upper()} {loc} {v.message}")
        for v in fixed_violations:
            loc = f"{v.file_path}:{v.line}" if v.line > 0 else v.file_path
            lines.append(f"FIXED {v.severity.upper()} {loc} {v.message}")

        lines.append(f"\n{len(new_violations)} new, {len(fixed_violations)} fixed")
        chat.add_entry(AssistantText("\n".join(lines)))

    def _cmd_search(self, args: str) -> None:
        """Cross-session full-text search via SQLite FTS5 + current session fallback."""
        chat = self.query_one(ChatScrollView)
        if not args:
            chat.add_entry(AssistantText("Usage: /search <query>"))
            return

        lines: list[str] = []

        # 1. Search across ALL sessions via SQLite FTS5
        try:
            from llm_code.runtime.conversation_db import ConversationDB
            db = ConversationDB()
            # Escape FTS5 special chars to prevent syntax errors
            safe_query = self._escape_fts5(args)
            db_results = db.search(safe_query, limit=20)
            for r in db_results:
                session_label = r.conversation_name or r.conversation_id[:8]
                date_str = r.created_at[:10] if r.created_at else ""
                snippet = r.content_snippet.replace(">>>", "**").replace("<<<", "**")
                role_icon = ">" if r.role == "user" else "<"
                lines.append(f"  {role_icon} [{date_str}] ({session_label}) {snippet}")
            db.close()
        except Exception:
            pass

        # 2. Fallback: search current session in-memory
        if not lines and self._runtime:
            for msg in self._runtime.session.messages:
                text = " ".join(
                    getattr(b, "text", "") for b in msg.content
                    if hasattr(b, "text")
                )
                if args.lower() in text.lower():
                    role_icon = ">" if msg.role == "user" else "<"
                    lines.append(f"  {role_icon} [current] {text[:120]}")

        if lines:
            header = f"Found {len(lines)} match(es) for \"{args}\""
            if len(lines) > 20:
                header += " (showing first 20)"
            chat.add_entry(AssistantText(header + ":\n" + "\n".join(lines[:20])))
        else:
            chat.add_entry(AssistantText(f"No matches for: {args}"))

    @staticmethod
    def _escape_fts5(query: str) -> str:
        """Escape special FTS5 characters to prevent query syntax errors."""
        # FTS5 special: AND OR NOT ( ) * " ^
        # Wrap each token in double quotes for exact matching
        tokens = query.split()
        if not tokens:
            return query
        return " ".join(f'"{t}"' for t in tokens)

    def _cmd_settings(self, args: str) -> None:
        """Open the read-only settings panel (Tier 2 wiring)."""
        from textual.screen import ModalScreen
        from textual.containers import VerticalScroll
        from textual.widgets import Static
        from llm_code.tui.settings_modal import (
            build_settings_sections, render_sections_text,
        )

        runtime_like = type("_RT", (), {
            "model": getattr(self._config, "model", "") if self._config else "",
            "permission_mode": getattr(self._config, "permission_mode", "") if self._config else "",
            "plan_mode": self._plan_mode,
            "config": self._config,
            "cost_tracker": self._cost_tracker,
            "keybindings": None,
            "active_skills": [],
        })()
        sections = build_settings_sections(runtime_like)
        body = render_sections_text(sections)

        class SettingsScreen(ModalScreen):
            DEFAULT_CSS = """
            SettingsScreen { align: center middle; }
            #settings-box {
                width: 80%;
                height: 80%;
                background: $surface;
                border: round $accent;
                padding: 1 2;
            }
            #settings-footer { dock: bottom; height: 1; color: $text-muted; text-align: center; }
            """

            def compose(self):
                with VerticalScroll(id="settings-box"):
                    yield Static(body)
                yield Static("Esc close", id="settings-footer")

            def on_key(self, event) -> None:
                if event.key == "escape":
                    self.dismiss()
                    event.prevent_default()
                    event.stop()

        self.push_screen(SettingsScreen())

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

    def _cmd_personas(self, args: str) -> None:
        """List available built-in agent personas for the swarm."""
        chat = self.query_one(ChatScrollView)
        from llm_code.swarm.personas import BUILTIN_PERSONAS

        lines = ["Available built-in personas:", ""]
        for name in sorted(BUILTIN_PERSONAS):
            persona = BUILTIN_PERSONAS[name]
            lines.append(f"  /{name:18s} — {persona.description}")
        chat.add_entry(AssistantText("\n".join(lines)))

    def _cmd_orchestrate(self, args: str) -> None:
        """Run the OrchestratorHook with inline LLM execution per persona."""
        chat = self.query_one(ChatScrollView)
        task = args.strip()
        if not task:
            chat.add_entry(AssistantText(
                "Usage: /orchestrate <task description>\n"
                "Routes the task to a persona by category and retries with "
                "fallback personas on failure."
            ))
            return
        if self._runtime is None:
            chat.add_entry(AssistantText("Orchestrate: runtime not ready."))
            return
        self.run_worker(self._run_orchestrate(task), name="orchestrate")

    async def _run_orchestrate(self, task: str) -> None:
        chat = self.query_one(ChatScrollView)
        try:
            from llm_code.swarm.orchestrator_hook import OrchestratorHook, categorize
            from llm_code.runtime.orchestrate_executor import (
                make_inline_persona_executor,
                sync_wrap,
            )

            runtime = self._runtime
            executor = make_inline_persona_executor(runtime)
            hook = OrchestratorHook(executor=sync_wrap(executor))
            # Run blocking orchestrate in thread to avoid blocking UI loop.
            import asyncio
            result = await asyncio.to_thread(hook.orchestrate, task)
            category = categorize(task)

            success_attempt = next((a for a in result.attempts if a.success), None)
            if success_attempt is not None:
                chat.add_entry(SkillBadge([success_attempt.persona]))
                chat.add_entry(AssistantText(result.final_output))
            else:
                lines = [f"Orchestrate failed (category={category}):", ""]
                for a in result.attempts:
                    lines.append(f"  attempt {a.attempt}: {a.persona} -> FAIL: {a.error}")
                chat.add_entry(AssistantText("\n".join(lines)))
        except Exception as exc:
            chat.add_entry(AssistantText(f"Orchestrate failed: {exc}"))

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
                recordings_dir = Path.home() / ".llmcode" / "recordings"
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
            recordings_dir = Path.home() / ".llmcode" / "recordings"
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
        checkpoints_dir = Path.home() / ".llmcode" / "checkpoints"
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
            elif sub == "lint":
                flags = parts[1] if len(parts) > 1 else ""
                if "--deep" in flags:
                    import asyncio
                    asyncio.ensure_future(self._memory_lint_deep())
                elif "--fix" in flags:
                    import asyncio
                    asyncio.ensure_future(self._memory_lint_fix())
                else:
                    self._memory_lint_fast()
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

    def _memory_lint_fast(self) -> None:
        """Run fast computational memory lint."""
        chat = self.query_one(ChatScrollView)
        try:
            from llm_code.runtime.memory_lint import lint_memory
            result = lint_memory(memory_dir=self._memory._dir, cwd=self._cwd)
            report = result.format_report()
            if not result.stale and not result.coverage_gaps and not result.old:
                report += "\n\nContradictions: (requires LLM, skipped — use /memory lint --deep)"
            chat.add_entry(AssistantText(report))
        except Exception as exc:
            chat.add_entry(AssistantText(f"Lint failed: {exc}"))

    async def _memory_lint_deep(self) -> None:
        """Run deep memory lint with LLM contradiction detection."""
        chat = self.query_one(ChatScrollView)
        chat.add_entry(AssistantText("Running deep memory lint..."))
        try:
            from llm_code.runtime.memory_lint import lint_memory_deep
            provider = self._runtime._provider if self._runtime else None
            result = await lint_memory_deep(
                memory_dir=self._memory._dir,
                cwd=self._cwd,
                llm_provider=provider,
            )
            chat.add_entry(AssistantText(result.format_report()))
        except Exception as exc:
            chat.add_entry(AssistantText(f"Deep lint failed: {exc}"))

    async def _memory_lint_fix(self) -> None:
        """Run lint and auto-remove stale references."""
        chat = self.query_one(ChatScrollView)
        try:
            from llm_code.runtime.memory_lint import lint_memory
            result = lint_memory(memory_dir=self._memory._dir, cwd=self._cwd)
            if not result.stale:
                chat.add_entry(AssistantText("No stale references to fix."))
                return
            removed = 0
            for s in result.stale:
                self._memory.delete(s.key)
                removed += 1
            chat.add_entry(AssistantText(f"Removed {removed} stale entries.\n\n{result.format_report()}"))
        except Exception as exc:
            chat.add_entry(AssistantText(f"Lint fix failed: {exc}"))

    # ── Repo Map ─────────────────────────────────────────────────────

    def _cmd_map(self, args: str) -> None:
        """Show repo map."""
        from llm_code.runtime.repo_map import build_repo_map
        chat = self.query_one(ChatScrollView)

        try:
            repo_map = build_repo_map(self._cwd)
            compact = repo_map.to_compact(max_tokens=2000)
            if compact:
                chat.add_entry(AssistantText(f"# Repo Map\n{compact}"))
            else:
                chat.add_entry(AssistantText("No source files found."))
        except Exception as exc:
            chat.add_entry(AssistantText(f"Error building repo map: {exc}"))

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
            config_path = Path.home() / ".llmcode" / "config.json"
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
            config_path = Path.home() / ".llmcode" / "config.json"
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
            dest = Path.home() / ".llmcode" / "skills" / name
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
                        self._reload_skills()
                        chat.add_entry(AssistantText(f"Installed {name}. Activated."))
                    else:
                        logger.warning("Skill clone failed for %s: %s", repo, result.stderr[:200])
                        chat.add_entry(AssistantText("Clone failed. Check the repository URL."))
            except Exception as exc:
                chat.add_entry(AssistantText(f"Install failed: {exc}"))
        elif sub == "enable" and subargs:
            if not self._is_safe_name(subargs):
                chat.add_entry(AssistantText("Invalid skill name."))
                return
            marker = Path.home() / ".llmcode" / "skills" / subargs / ".disabled"
            marker.unlink(missing_ok=True)
            self._reload_skills()
            chat.add_entry(AssistantText(f"Enabled {subargs}"))
        elif sub == "disable" and subargs:
            if not self._is_safe_name(subargs):
                chat.add_entry(AssistantText("Invalid skill name."))
                return
            marker = Path.home() / ".llmcode" / "skills" / subargs / ".disabled"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
            self._reload_skills()
            chat.add_entry(AssistantText(f"Disabled {subargs}"))
        elif sub == "remove" and subargs:
            if not self._is_safe_name(subargs):
                chat.add_entry(AssistantText("Invalid skill name."))
                return
            d = Path.home() / ".llmcode" / "skills" / subargs
            if d.is_dir():
                shutil.rmtree(d)
                self._reload_skills()
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
                    enabled=not (Path.home() / ".llmcode" / "skills" / s.name / ".disabled").exists(),
                    repo="",
                    extra=mode,
                ))

            # Installed plugins (check filesystem for newly installed)
            try:
                from llm_code.marketplace.installer import PluginInstaller
                pi = PluginInstaller(Path.home() / ".llmcode" / "plugins")
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

            # Marketplace plugins — not yet installed
            for p in get_all_known_plugins():
                if p["name"] not in installed_names:
                    skill_count = p.get("skills", 0)
                    extra = f"{skill_count} skills" if skill_count > 0 else p.get("type", "plugin")
                    items.append(MarketplaceItem(
                        name=p["name"],
                        description=p.get("desc", ""),
                        source=p.get("source", "official"),
                        installed=False,
                        repo=p.get("repo", ""),
                        extra=extra,
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
            installer = PluginInstaller(Path.home() / ".llmcode" / "plugins")
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
            dest = Path.home() / ".llmcode" / "plugins" / name
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
                    self._reload_skills()
                    chat.add_entry(AssistantText(f"Installed {name}. Activated."))
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
                self._reload_skills()
                chat.add_entry(AssistantText(f"Enabled {subargs}"))
            except Exception as exc:
                chat.add_entry(AssistantText(f"Enable failed: {exc}"))
        elif sub == "disable" and subargs:
            if not self._is_safe_name(subargs):
                chat.add_entry(AssistantText("Invalid plugin name."))
                return
            try:
                installer.disable(subargs)
                self._reload_skills()
                chat.add_entry(AssistantText(f"Disabled {subargs}"))
            except Exception as exc:
                chat.add_entry(AssistantText(f"Disable failed: {exc}"))
        elif sub in ("remove", "uninstall") and subargs:
            if not self._is_safe_name(subargs):
                chat.add_entry(AssistantText("Invalid plugin name."))
                return
            try:
                installer.uninstall(subargs)
                self._reload_skills()
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
                    extra = f"{skills_count} skills" if skills_count > 0 else p.get("type", "plugin")
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
        from llm_code.tui.chat_view import AssistantText

        chat = self.query_one(ChatScrollView)
        item = event.item
        action = event.action

        if action == "install":
            if item.source == "npm":
                self._cmd_mcp(f"install {item.name}")
            elif item.repo:
                # If plugin has a subdir, install from marketplace repo subdirectory
                subdir = getattr(item, "extra_data", {}).get("subdir", "") if hasattr(item, "extra_data") else ""
                # Check registry for subdir info
                from llm_code.marketplace.builtin_registry import get_all_known_plugins
                for p in get_all_known_plugins():
                    if p["name"] == item.name and p.get("subdir"):
                        subdir = p["subdir"]
                        break
                if subdir:
                    self._install_from_marketplace(item.name, item.repo, subdir)
                elif item.source in ("official", "community"):
                    self._cmd_plugin(f"install {item.repo}")
                else:
                    self._cmd_skill(f"install {item.repo}")
            else:
                chat.add_entry(AssistantText(
                    f"No install URL for {item.name}. "
                    f"Try: /skill install owner/{item.name}"
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
        # Return focus to InputBar after marketplace action
        self.query_one(InputBar).focus()
