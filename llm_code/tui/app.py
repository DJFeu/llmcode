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
    from llm_code.runtime.config import RuntimeConfig
    from llm_code.tools.registry import ToolRegistry
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
# New (empty-response-diagnostics): distinct variant for "the model
# emitted N tokens but none of them landed in the visible buffer nor
# the thinking buffer". This is different from the thinking-exhausted
# case: here we KNOW tokens came back, we just couldn't classify them.
# Typical causes: malformed <think> tags that slipped past the parser,
# a partial tool_call that got stripped but not dispatched, or a
# truncated response caused by a low max_tokens / thinking_budget cap.
_EMPTY_RESPONSE_UNCLASSIFIED_EN = (
    "(The model emitted {n} output token(s) but none were visible text, "
    "thinking, or a dispatched tool call. This is usually a truncated "
    "response — check max_tokens / thinking_budget or rerun with -v to "
    "capture the raw stream.)"
)
_EMPTY_RESPONSE_UNCLASSIFIED_ZH = (
    "(模型輸出了 {n} 個 token,但全部都不是可見文字、thinking 內容,"
    "也不是成功派發的工具呼叫。通常是輸出被截斷 — 檢查 max_tokens / "
    "thinking_budget 設定,或用 -v 重跑以擷取 raw stream。)"
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


def _truncation_warning_message(
    *,
    stop_reason: str,
    turn_output_tokens: int,
    user_input: str,
    session_messages: Any = None,
) -> str:
    """Wave: truncation warning shown when the model hit its output
    token cap mid-generation (``stop_reason`` in ``("length",
    "max_tokens")``). Kept as a pure helper so tests can exercise
    the i18n logic without spinning up the full TUI event loop.
    """
    zh = _session_is_cjk(user_input, session_messages)
    if zh:
        return (
            f"(⚠ 回應被截斷 — 模型達到輸出上限 ({stop_reason})。"
            f"實際輸出 {turn_output_tokens} tokens。"
            f"試試加長 max_tokens 或 context window,或重新提問。)"
        )
    return (
        f"(⚠ Response truncated — the model hit its output "
        f"token cap ({stop_reason}) after {turn_output_tokens} "
        f"tokens. Try increasing max_tokens / context window "
        f"or rephrasing.)"
    )


def _empty_response_message(
    *,
    saw_tool_call: bool,
    user_input: str,
    session_messages: Any = None,
    turn_output_tokens: int = 0,
    thinking_buffer_len: int = 0,
) -> str:
    """Pick the right empty-response diagnostic, matching the user's
    language (CJK vs non-CJK) and the *reason* the visible buffer is
    empty.

    Decision tree:

    1. The model dispatched a tool call (``saw_tool_call=True``) but
       produced no visible reply → tool-call variant. This is the
       common case where the model tried to call a tool for a query
       that didn't actually need one.
    2. The model emitted some output tokens AND we captured nothing
       in the thinking buffer either → ``unclassified`` variant. We
       know tokens came back but could not route them anywhere — this
       is usually a truncated response. The message includes the
       token count so the user can compare against their configured
       ``max_tokens`` / ``thinking_budget``.
    3. Otherwise → the classic "thinking exhausted the budget"
       variant. This is the thinking-mode misconfiguration path.
    """
    zh = _session_is_cjk(user_input, session_messages)
    if saw_tool_call:
        return _EMPTY_RESPONSE_TOOL_CALL_ZH if zh else _EMPTY_RESPONSE_TOOL_CALL_EN
    # Tokens came back but we classified none of them — pick the
    # unclassified variant so the user knows the token counter is
    # non-zero and can check max_tokens.
    if turn_output_tokens > 0 and thinking_buffer_len == 0:
        template = (
            _EMPTY_RESPONSE_UNCLASSIFIED_ZH if zh else _EMPTY_RESPONSE_UNCLASSIFIED_EN
        )
        return template.format(n=turn_output_tokens)
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
        BashTool(
            default_timeout=bash_timeout,
            compress_output=config.output_compression,
            sandbox=_make_sandbox(config),
        ),
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


def _make_sandbox(config):
    """Create a DockerSandbox if sandbox is configured."""
    try:
        sandbox_cfg = getattr(config, "sandbox", None)
        if sandbox_cfg is not None and getattr(sandbox_cfg, "enabled", False):
            from llm_code.sandbox.docker_sandbox import DockerSandbox
            return DockerSandbox(sandbox_cfg)
    except Exception:
        pass
    return None


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
        from llm_code.tui.command_dispatcher import CommandDispatcher
        self._cmd_dispatcher = CommandDispatcher(self)
        self._config = config
        self._cwd = cwd or Path.cwd()
        self._budget = budget
        self._initial_mode = initial_mode
        self._runtime = None
        self._cost_tracker = None
        self._input_tokens = 0
        self._output_tokens = 0
        # Latest StreamMessageStop.stop_reason captured per turn.
        # Used by the truncation warning + empty-response log so
        # the TUI can explain *why* a reply looked short.
        self._last_stop_reason: str = "unknown"
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
        self._loaded_plugins: dict[str, object] = {}  # name → LoadedPlugin handle
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
        """Show styled welcome banner with gradient logo in chat area."""
        import sys
        from textual.widgets import Static
        from rich.color import Color
        from rich.style import Style
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

        # Gradient: bright cyan (#7DF9FF) → deep navy (#0A4BA0)
        gradient_colors = [
            (125, 249, 255),  # bright cyan
            (105, 230, 248),  # light cyan
            (85, 205, 240),   # cyan
            (65, 180, 232),   # sky blue
            (48, 155, 222),   # blue
            (42, 148, 220),   # blue-medium
            (38, 138, 215),   # medium blue
            (30, 120, 205),   # deeper blue
            (22, 105, 192),   # royal blue
            (15, 88, 178),    # deep blue
            (10, 75, 160),    # navy
            (8, 65, 145),     # deep navy
        ]

        model = self._config.model if self._config else "(not set)"
        branch = self._detect_branch()
        workspace = self._cwd.name
        if branch:
            workspace += f" · {branch}"
        perm = self._config.permission_mode if self._config else "prompt"
        paste_key = "Cmd+V to paste" if sys.platform == "darwin" else "Ctrl+V to paste"

        text = RichText()
        for i, line in enumerate(logo_lines):
            r, g, b = gradient_colors[i]
            text.append(line + "\n", style=Style(color=Color.from_rgb(r, g, b), bold=True))
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

        # Background version check (non-blocking, cached 6h)
        async def _check_version() -> None:
            try:
                from llm_code.cli.updater import check_update_background
                hint = await check_update_background()
                if hint:
                    chat.add_entry(AssistantText(f"  {hint}"))
            except Exception:
                pass

        self.run_worker(_check_version(), name="version_check")

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

    def _load_plugin_tools(self, plugin_dir: Path, chat: "ChatScrollView") -> None:
        """Load Python tools from a plugin's manifest into the tool registry.

        Stores the LoadedPlugin handle in ``_loaded_plugins`` so
        ``_unload_plugin_tools`` can reverse the load on disable/remove.
        """
        try:
            from llm_code.marketplace.plugin import PluginManifest
            from llm_code.marketplace.executor import load_plugin, PluginConflictError, PluginLoadError
            manifest = PluginManifest.from_path(plugin_dir)
            if not manifest.provides_tools:
                return
            if self._tool_reg is None:
                return
            handle = load_plugin(
                manifest,
                plugin_dir,
                tool_registry=self._tool_reg,
                skill_router=getattr(self._runtime, "_skill_router", None),
                known_tool_names={t.name for t in self._tool_reg.all_tools()},
            )
            self._loaded_plugins[manifest.name] = handle
            if handle.tool_names:
                chat.add_entry(AssistantText(
                    f"Loaded {len(handle.tool_names)} tool(s) from plugin: "
                    f"{', '.join(handle.tool_names)}"
                ))
        except FileNotFoundError:
            pass
        except (PluginConflictError, PluginLoadError) as exc:
            chat.add_entry(AssistantText(f"Plugin tool load warning: {exc}"))
        except Exception as exc:
            logger.debug("Plugin tool load failed for %s: %s", plugin_dir, exc)

    def _unload_plugin_tools(self, name: str, chat: "ChatScrollView") -> None:
        """Reverse a previous ``_load_plugin_tools`` call."""
        handle = self._loaded_plugins.pop(name, None)
        if handle is None or self._tool_reg is None:
            return
        try:
            from llm_code.marketplace.executor import unload_plugin
            unload_plugin(
                handle,
                tool_registry=self._tool_reg,
                skill_router=getattr(self._runtime, "_skill_router", None),
            )
            chat.add_entry(AssistantText(f"Unloaded tools from plugin: {name}"))
        except Exception as exc:
            logger.debug("Plugin tool unload failed for %s: %s", name, exc)

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
        from llm_code.tools.registry import ToolRegistry

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

        # Run daily memory distillation (today-*.md → recent.md → archive.md)
        try:
            from llm_code.runtime.memory_layers import distill_daily
            from datetime import date as _date
            _mem_dir = Path.home() / ".llmcode" / "memory"
            if _mem_dir.is_dir():
                distill_daily(_mem_dir, _date.today())
        except Exception:
            pass  # non-critical — skip silently

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

        # Create TextualDialogs for modal screen prompts
        from llm_code.tui.dialogs.textual_backend import TextualDialogs
        self._dialogs = TextualDialogs(self)

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
            dialogs=self._dialogs,
        )
        # Register plan mode tools (need runtime reference)
        try:
            from llm_code.tools.plan_mode import EnterPlanModeTool, ExitPlanModeTool
            if self._tool_reg:
                self._tool_reg.register(EnterPlanModeTool(runtime=self._runtime))
                self._tool_reg.register(ExitPlanModeTool(runtime=self._runtime))
        except Exception:
            pass

        # Install MCP event sink so non-root server spawns surface an
        # inline approval widget.
        try:
            self._runtime.set_mcp_event_sink(self._on_mcp_approval_event)
        except Exception:
            pass

    def _on_mcp_approval_event(self, event) -> None:
        """Sink called by ConversationRuntime.request_mcp_approval.

        Schedules an async modal dialog; resolution flows back through
        ``runtime.send_mcp_approval_response`` when the user picks an option.
        """
        import asyncio
        asyncio.ensure_future(self._show_mcp_approval_dialog(event))

    async def _show_mcp_approval_dialog(self, event) -> None:
        """Show MCP approval as a TextualDialogs select modal."""
        _prompt = f"MCP Server: {event.server_name}"
        if event.command:
            _prompt += f"\nCommand: {event.command}"
        if event.description:
            _prompt += f"\n{event.description}"
        from llm_code.tui.dialogs import Choice
        _choices = [
            Choice(value="allow", label="Allow (y)", hint="Allow this MCP request"),
            Choice(value="always", label="Always allow (a)", hint="Auto-allow from this server"),
            Choice(value="deny", label="Deny (n)", hint="Reject this request"),
        ]
        try:
            result = await self._dialogs.select(_prompt, _choices, default="allow")
            if self._runtime is not None:
                self._runtime.send_mcp_approval_response(result)
        except Exception:
            if self._runtime is not None:
                self._runtime.send_mcp_approval_response("deny")

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
        """Handle Ctrl+P quick open, Ctrl+V verbose toggle."""
        if event.key == "ctrl+p":
            self._open_quick_open()
            event.prevent_default()
            event.stop()
            return
        if event.key == "ctrl+v":
            if self._toggle_last_error_verbose():
                event.prevent_default()
                event.stop()
            return

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
            SpinnerLine, ThinkingBlock, ToolBlock, TurnSummary,
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
        # Canonical stream parser: single state machine shared with the
        # runtime dispatch path (llm_code.streaming.stream_parser). Emits
        # TEXT / THINKING / TOOL_CALL events that we route into the TUI
        # widgets below. Replaced ~110 lines of inline tag parsing.
        from llm_code.streaming.stream_parser import StreamEventKind, StreamParser
        # Auto-detect implicit thinking: if the runtime's config has
        # thinking mode enabled, the vLLM chat template likely injects
        # <think>\n into the assistant prompt prefix so only the
        # closing tag appears in the stream. Starting the parser in
        # implicit_thinking mode ensures early content is classified
        # as THINKING, not TEXT — avoiding the retroactive
        # reclassification problem (#8 StreamParser implicit-think-end).
        # Read implicit_thinking from the model profile (authoritative)
        # instead of probing config.thinking.mode.
        _profile = getattr(self._runtime, "_model_profile", None)
        _implicit_thinking = _profile.implicit_thinking if _profile else False
        # Pass known tool names so the parser detects bare <tool_name>
        # tags (variant 5) and classifies them as TOOL_CALL, not TEXT.
        _tool_names = frozenset()
        if self._tool_reg:
            _tool_names = frozenset(t.name for t in self._tool_reg.all_tools())
        _stream_parser = StreamParser(
            implicit_thinking=_implicit_thinking,
            known_tool_names=_tool_names,
        )
        _saw_tool_call_this_turn = False  # For empty-response diagnosis

        async def remove_spinner() -> None:
            """Remove spinner if it is currently mounted."""
            if spinner.is_mounted:
                await spinner.remove()

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
                if isinstance(event, StreamTextDelta):
                    # Delegate all <think> / <tool_call> tag recognition
                    # to the shared StreamParser. It produces TEXT,
                    # THINKING, and TOOL_CALL events that we route into
                    # TUI widgets below.
                    for parsed_ev in _stream_parser.feed(event.text):
                        if parsed_ev.kind == StreamEventKind.THINKING:
                            if not thinking_buffer:
                                # First thinking content this turn — start
                                # the elapsed timer and set spinner phase.
                                thinking_start = time.monotonic()
                                spinner.phase = "thinking"
                            thinking_buffer += parsed_ev.text
                        elif parsed_ev.kind == StreamEventKind.TEXT:
                            # Flush any pending thinking into a ThinkingBlock
                            # before rendering visible text.
                            if thinking_buffer.strip():
                                elapsed_t = time.monotonic() - thinking_start
                                tokens_t = len(thinking_buffer) // 4
                                chat.add_entry(
                                    ThinkingBlock(thinking_buffer, elapsed_t, tokens_t)
                                )
                                thinking_buffer = ""
                            if parsed_ev.text:
                                if not assistant_added:
                                    await remove_spinner()
                                    chat.add_entry(assistant)
                                    assistant_added = True
                                assistant.append_text(parsed_ev.text)
                        elif parsed_ev.kind == StreamEventKind.TOOL_CALL:
                            # Flush any pending thinking before the tool
                            # call (so it's rendered in the right order).
                            if thinking_buffer.strip():
                                elapsed_t = time.monotonic() - thinking_start
                                tokens_t = len(thinking_buffer) // 4
                                chat.add_entry(
                                    ThinkingBlock(thinking_buffer, elapsed_t, tokens_t)
                                )
                                thinking_buffer = ""
                            # The runtime parser will re-detect and
                            # dispatch the call; TUI just records the
                            # fact for the empty-response diagnostic.
                            _saw_tool_call_this_turn = True
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
                    # Show permission as a modal select dialog
                    _perm_prompt = f"Tool: {event.tool_name}"
                    if event.args_preview:
                        _perm_prompt += f"\n{event.args_preview}"
                    if event.diff_lines:
                        _perm_prompt += "\n" + "\n".join(event.diff_lines[:10])
                    if event.pending_files:
                        _perm_prompt += "\nFiles: " + ", ".join(event.pending_files[:5])
                    from llm_code.tui.dialogs import Choice
                    _perm_choices = [
                        Choice(value="allow", label="Allow (y)", hint="Allow this tool call"),
                        Choice(value="always_kind", label="Always allow this type (a)", hint="Auto-allow this tool kind"),
                        Choice(value="always_exact", label="Always allow exact (A)", hint="Auto-allow this exact tool+args"),
                        Choice(value="edit", label="Edit args (e)", hint="Edit tool arguments before running"),
                        Choice(value="deny", label="Deny (n)", hint="Reject this tool call"),
                    ]
                    try:
                        _perm_result = await self._dialogs.select(
                            _perm_prompt, _perm_choices, default="allow",
                        )
                        if _perm_result == "edit":
                            # Open a text editor with the current args as JSON
                            import json as _json
                            _edited = await self._dialogs.text(
                                f"Edit args for {event.tool_name}:",
                                default=event.args_preview or "{}",
                                multiline=True,
                            )
                            try:
                                _parsed = _json.loads(_edited)
                                self._runtime.send_permission_response("edit", edited_args=_parsed)
                            except _json.JSONDecodeError:
                                chat.add_entry(AssistantText("Invalid JSON — running with original args."))
                                self._runtime.send_permission_response("allow")
                        else:
                            self._runtime.send_permission_response(_perm_result)
                    except Exception:
                        self._runtime.send_permission_response("deny")

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
                    # Capture the provider's stop_reason so the turn-
                    # end diagnostics can tell the user *why* the
                    # model stopped. Previously this field was read
                    # only by the runtime's auto-upgrade logic and
                    # never surfaced to the TUI, so a truncation
                    # that slipped past that path was invisible.
                    self._last_stop_reason = event.stop_reason or "unknown"
                    if event.usage:
                        turn_input_tokens += event.usage.input_tokens
                        turn_output_tokens += event.usage.output_tokens
                        self._input_tokens += event.usage.input_tokens
                        self._output_tokens += event.usage.output_tokens
                        if self._cost_tracker:
                            # Wave2-2: forward cache token buckets so cache
                            # reads (10% of input price) and cache writes
                            # (125%) are priced correctly instead of being
                            # counted as zero.
                            self._cost_tracker.add_usage(
                                event.usage.input_tokens,
                                event.usage.output_tokens,
                                cache_read_tokens=getattr(event.usage, "cache_read_tokens", 0),
                                cache_creation_tokens=getattr(event.usage, "cache_creation_tokens", 0),
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
            try:
                await remove_spinner()
            except Exception:
                pass
            input_bar.disabled = False
            status.is_streaming = False

        # Flush any remaining buffered content from the shared parser
        for parsed_ev in _stream_parser.flush():
            if parsed_ev.kind == StreamEventKind.THINKING:
                thinking_buffer += parsed_ev.text
            elif parsed_ev.kind == StreamEventKind.TEXT and parsed_ev.text:
                if not assistant_added:
                    chat.add_entry(assistant)
                    assistant_added = True
                assistant.append_text(parsed_ev.text)
            elif parsed_ev.kind == StreamEventKind.TOOL_CALL:
                _saw_tool_call_this_turn = True

        if thinking_buffer.strip():
            elapsed_t = time.monotonic() - thinking_start
            tokens_t = len(thinking_buffer) // 4
            chat.add_entry(ThinkingBlock(thinking_buffer, elapsed_t, tokens_t))

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
            #
            # Diagnostic log: everything the user might need to debug
            # the empty-response cause is captured in a single warning
            # line so `-v` runs have the full state immediately.
            _thinking_len = len(thinking_buffer)
            logger.warning(
                "empty response fallback: out_tokens=%d thinking_len=%d "
                "saw_tool_call=%s assistant_added=%s stop_reason=%s "
                "thinking_head=%r",
                turn_output_tokens,
                _thinking_len,
                _saw_tool_call_this_turn,
                assistant_added,
                getattr(self, "_last_stop_reason", "unknown"),
                thinking_buffer[:120],
            )
            chat.add_entry(AssistantText(
                _empty_response_message(
                    saw_tool_call=_saw_tool_call_this_turn,
                    user_input=user_input,
                    session_messages=getattr(self._runtime, "session", None) and self._runtime.session.messages,
                    turn_output_tokens=turn_output_tokens,
                    thinking_buffer_len=_thinking_len,
                )
            ))

        # Unconditional turn-end diagnostic so every turn (not just
        # the empty-response path) has a single log line capturing
        # the full state. Useful for "my reply seems truncated"
        # reports where the TUI shows SOME text but the user suspects
        # content went missing.
        _stop_reason = getattr(self, "_last_stop_reason", "unknown")
        logger.debug(
            "turn complete: out_tokens=%d thinking_len=%d "
            "assistant_added=%s saw_tool_call=%s stop_reason=%s",
            turn_output_tokens,
            len(thinking_buffer),
            assistant_added,
            _saw_tool_call_this_turn,
            _stop_reason,
        )

        # Truncation warning: if the provider reported finish_reason
        # == "length" / "max_tokens" AND some visible content was
        # shown (so the empty-response fallback didn't fire), the
        # user's reply was cut off mid-generation. The runtime's
        # auto-upgrade path handles most cases but a provider that
        # caps hard can still leak through. Show a subtle but
        # explicit warning so the user knows what happened instead
        # of puzzling over a truncated list.
        if assistant_added and _stop_reason in ("length", "max_tokens"):
            warn_text = _truncation_warning_message(
                stop_reason=_stop_reason,
                turn_output_tokens=turn_output_tokens,
                user_input=user_input,
                session_messages=getattr(self._runtime, "session", None) and self._runtime.session.messages,
            )
            chat.add_entry(AssistantText(warn_text))
            logger.warning(
                "truncation warning shown: out_tokens=%d stop_reason=%s",
                turn_output_tokens, _stop_reason,
            )

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

        if self._cmd_dispatcher.dispatch(name, args):
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

    # ── Marketplace ItemAction handler ────────────────────────────────

    def on_marketplace_browser_item_action(
        self, event: "MarketplaceBrowser.ItemAction"
    ) -> None:
        """Handle marketplace item selection (install/enable/disable/remove)."""
        from llm_code.tui.chat_view import AssistantText

        chat = self.query_one(ChatScrollView)
        item = event.item
        action = event.action

        dispatcher = self._cmd_dispatcher
        if action == "install":
            if item.source == "npm":
                dispatcher._cmd_mcp(f"install {item.name}")
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
                    dispatcher._cmd_plugin(f"install {item.repo}")
                else:
                    dispatcher._cmd_skill(f"install {item.repo}")
            else:
                chat.add_entry(AssistantText(
                    f"No install URL for {item.name}. "
                    f"Try: /skill install owner/{item.name}"
                ))
        elif action == "enable":
            if item.source in ("official", "community", "installed"):
                dispatcher._cmd_plugin(f"enable {item.name}")
            else:
                dispatcher._cmd_skill(f"enable {item.name}")
        elif action == "disable":
            if item.source in ("official", "community", "installed"):
                dispatcher._cmd_plugin(f"disable {item.name}")
            else:
                dispatcher._cmd_skill(f"disable {item.name}")
        elif action == "remove":
            if item.source in ("official", "community", "installed"):
                dispatcher._cmd_plugin(f"remove {item.name}")
            else:
                dispatcher._cmd_skill(f"remove {item.name}")
        # Return focus to InputBar after marketplace action
        self.query_one(InputBar).focus()
