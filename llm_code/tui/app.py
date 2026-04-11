# llm_code/tui/app.py
"""LLMCodeTUI — Textual fullscreen app composing all widgets."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding

from llm_code.tui.chat_view import ChatScrollView, UserMessage, AssistantText
from llm_code.tui.header_bar import HeaderBar
from llm_code.tui.input_bar import InputBar
from llm_code.tui.status_bar import StatusBar
from llm_code.tui.streaming_handler import StreamingHandler
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

    Uses the centralized ``builtin.get_builtin_tools()`` registry so
    adding a new core tool requires only a single entry there.
    """
    from llm_code.tools.bash import BashTool
    from llm_code.tools.builtin import get_builtin_tools

    base_url = config.provider_base_url or ""
    is_local = any(
        h in base_url
        for h in ("localhost", "127.0.0.1", "0.0.0.0", "192.168.", "10.", "172.")
    )
    bash_timeout = 0 if is_local else 30  # 0 = no timeout for local models

    # BashTool needs special constructor args — build it separately.
    bash_kwargs = dict(
        default_timeout=bash_timeout,
        compress_output=config.output_compression,
        sandbox=_make_sandbox(config),
    )

    for name, cls in get_builtin_tools().items():
        try:
            if cls is BashTool:
                registry.register(cls(**bash_kwargs))
            else:
                registry.register(cls())
        except ValueError:
            pass


def _make_sandbox(config):
    """Create a DockerSandbox if sandbox is configured."""
    try:
        sandbox_cfg = getattr(config, "sandbox", None)
        if sandbox_cfg is not None and getattr(sandbox_cfg, "enabled", False):
            from llm_code.tools.sandbox import DockerSandbox
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
        from llm_code.tui.runtime_init import RuntimeInitializer
        self._cmd_dispatcher = CommandDispatcher(self)
        self._runtime_initializer = RuntimeInitializer(self)
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
        self._voice_recorder = None  # AudioRecorder | None — live during /voice on
        self._voice_stt = None  # STTEngine | None — cached across toggles
        self._voice_monitor_timer = None  # Textual Timer for status-bar + VAD poll
        self._vcr_recorder = None
        self._interrupt_pending: bool = False
        self._last_interrupt_time: float = 0.0
        self._analysis_context: str | None = None
        self._context_warned: bool = False  # one-shot 80% warning
        self._streaming = StreamingHandler(self)

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
        quick_rows = [
            ("Quick start", "/help · /skill · /mcp"),
            ("Multiline", "Shift+Enter or Ctrl+J"),
            ("Images", paste_key),
            ("Scroll", "PageUp/Down · Shift+↑/↓"),
            ("Cycle agent", "Shift+Tab or Ctrl+Y (build/plan/suggest)"),
        ]
        # Only surface the voice hotkey when voice is actually enabled
        # in config — otherwise the hint is just noise for the 95% of
        # users who never touch it. Falls back to "ctrl+g" default so
        # the hint still lists something sensible if hotkey is unset.
        voice_cfg = getattr(self._config, "voice", None) if self._config else None
        if voice_cfg and getattr(voice_cfg, "enabled", False):
            hotkey = (getattr(voice_cfg, "hotkey", "") or "ctrl+g").strip()
            # Nicer-looking display: "ctrl+g" → "Ctrl+G"
            pretty = "+".join(part.capitalize() for part in hotkey.split("+"))
            quick_rows.append(
                ("Voice", f"{pretty} to start/stop (auto-stops on silence)")
            )
        for label, value in quick_rows:
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
        """Initialize the conversation runtime.

        Delegated to :class:`RuntimeInitializer` (see ``runtime_init.py``).
        """
        self._runtime_initializer.initialize()

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
        """Run a conversation turn — delegates to StreamingHandler."""
        await self._streaming.run_turn(user_input, images)

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

    # ── Voice recording monitor ───────────────────────────────────────

    def _start_voice_monitor(self) -> None:
        """Kick off the 200ms poll that updates the status-bar timer
        and runs VAD auto-stop detection.

        Safe to call repeatedly — any existing timer is torn down
        first so we never double-schedule. Called from
        ``CommandDispatcher._cmd_voice`` on `/voice on` and from the
        hotkey toggle.
        """
        self._stop_voice_monitor()
        self._voice_monitor_timer = self.set_interval(
            0.2, self._tick_voice_monitor
        )

    def _stop_voice_monitor(self) -> None:
        """Cancel the voice monitor timer and clear the status-bar
        elapsed reading. Idempotent."""
        if self._voice_monitor_timer is not None:
            try:
                self._voice_monitor_timer.stop()
            except Exception:
                pass
            self._voice_monitor_timer = None
        try:
            self.query_one(StatusBar).voice_elapsed = 0.0
        except Exception:
            pass

    def _tick_voice_monitor(self) -> None:
        """Timer callback: refresh elapsed on the status bar, and fire
        auto-stop when VAD says the speaker has gone quiet."""
        recorder = self._voice_recorder
        if recorder is None or not self._voice_active:
            self._stop_voice_monitor()
            return
        try:
            elapsed = recorder.elapsed_seconds()
        except Exception:
            elapsed = 0.0
        try:
            self.query_one(StatusBar).voice_elapsed = elapsed
        except Exception:
            pass
        try:
            if recorder.should_auto_stop():
                # Tear down via the same path /voice off uses, passing
                # through the dispatcher so chat messages + transcription
                # worker are scheduled consistently. The dispatcher
                # itself calls _stop_voice_monitor when it's done.
                self._cmd_dispatcher.dispatch("voice", "off")
        except Exception:
            # VAD failure must never crash the TUI.
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
