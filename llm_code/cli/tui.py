"""Textual-based TUI for llm-code — Claude Code style interface."""
from __future__ import annotations

import asyncio
import dataclasses
import os
import re
import subprocess
import time
from pathlib import Path

from rich.markup import escape
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, RichLog, Static

from llm_code.api.client import ProviderClient
from llm_code.api.types import (
    StreamMessageStop,
    StreamTextDelta,
    StreamToolExecResult,
    StreamToolExecStart,
    StreamToolProgress,
)
from llm_code.cli.commands import SlashCommand, parse_slash_command
from llm_code.runtime.config import RuntimeConfig, load_config
from llm_code.runtime.context import ProjectContext
from llm_code.runtime.conversation import ConversationRuntime
from llm_code.runtime.hooks import HookRunner
from llm_code.runtime.permissions import PermissionMode, PermissionPolicy
from llm_code.runtime.prompt import SystemPromptBuilder
from llm_code.runtime.session import Session, SessionManager
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
from llm_code.tools.read_file import ReadFileTool
from llm_code.tools.registry import ToolRegistry
from llm_code.tools.write_file import WriteFileTool


_TRUNCATION_THRESHOLD = 50  # lines before truncation notice
_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _format_tool_call(tool_name: str, args_summary: str) -> str:
    """Format tool call like Claude Code: Read(path) / Bash(cmd) / Edit(path)."""
    import json

    try:
        args = json.loads(args_summary.replace("'", '"'))
    except Exception:
        args = {}

    if not isinstance(args, dict):
        return f"{tool_name}({args_summary[:60]})"

    if "path" in args:
        display = str(args["path"])
    elif "command" in args:
        display = str(args["command"])[:80]
    elif "pattern" in args:
        display = str(args["pattern"])
    elif "key" in args:
        display = str(args["key"])
    elif "task" in args:
        display = str(args["task"])[:60]
    elif "message" in args:
        display = str(args["message"])[:60]
    else:
        vals = list(args.values())
        display = str(vals[0])[:60] if vals else ""

    parts = tool_name.split("_")
    pretty_name = "".join(p.capitalize() for p in parts)
    return f"{pretty_name}({display})"


def _detect_git_branch(cwd: Path) -> str:
    """Return current git branch name, or empty string."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return ""


class PermissionScreen(ModalScreen[str]):
    """Single-key permission dialog for tool execution."""

    BINDINGS = [
        Binding("y", "allow", "Allow"),
        Binding("n", "deny", "Deny"),
        Binding("a", "always", "Always allow"),
    ]

    DEFAULT_CSS = """
    PermissionScreen {
        align: center middle;
    }
    #perm-dialog {
        width: 60;
        height: auto;
        max-height: 16;
        border: heavy $warning;
        background: $surface;
        padding: 1 2;
    }
    """

    def __init__(self, tool_name: str, args_summary: str) -> None:
        super().__init__()
        self.tool_name = tool_name
        self.args_summary = args_summary

    def compose(self) -> ComposeResult:
        detail = self.args_summary[:200] if self.args_summary else "(no args)"
        yield Static(
            f"  [bold yellow]Warning[/] Permission required\n\n"
            f"  Tool: [bold]{escape(self.tool_name)}[/]\n"
            f"  Args: [dim]{escape(detail)}[/]\n\n"
            f"  [bold]\\[y][/] Allow  [bold]\\[n][/] Deny  [bold]\\[a][/] Always allow",
            id="perm-dialog",
        )

    def action_allow(self) -> None:
        self.dismiss("allow")

    def action_deny(self) -> None:
        self.dismiss("deny")

    def action_always(self) -> None:
        self.dismiss("always")


class MarketplaceBrowser(ModalScreen[str]):
    """Interactive browsable list for skills/mcp/plugins — Claude Code style."""

    BINDINGS = [
        Binding("escape", "dismiss_screen", "Close"),
        Binding("q", "dismiss_screen", "Close"),
    ]

    DEFAULT_CSS = """
    MarketplaceBrowser {
        align: center middle;
    }
    #browser-container {
        width: 80%;
        height: 80%;
        border: heavy $accent;
        background: $surface;
    }
    #browser-title {
        height: 3;
        padding: 1 2;
        background: $accent-darken-2;
    }
    #browser-list {
        height: 1fr;
    }
    #browser-footer {
        height: 3;
        padding: 0 2;
        background: $surface-darken-1;
    }
    """

    def __init__(self, title: str, items: list[tuple[str, str, bool]], actions: str = "") -> None:
        """items: list of (name, description, is_installed)"""
        super().__init__()
        self._title = title
        self._items = items
        self._actions = actions

    def compose(self) -> ComposeResult:
        from textual.containers import Vertical
        from textual.widgets import OptionList
        from textual.widgets.option_list import Option

        with Vertical(id="browser-container"):
            yield Static(
                f"[bold]{self._title}[/]  [dim]({len(self._items)} items · ↑↓ navigate · Enter select · Esc close)[/]",
                id="browser-title",
            )

            options = []
            for name, desc, installed in self._items:
                icon = "[green]●[/]" if installed else "[dim]○[/]"
                label = f"{icon} [bold]{name}[/]  [dim]· {desc}[/]"
                if installed:
                    label += " [green](installed)[/]"
                options.append(Option(label, id=name))

            yield OptionList(*options, id="browser-list")
            yield Static(
                f"[dim]{self._actions}[/]",
                id="browser-footer",
            )

    def on_option_list_option_selected(self, event) -> None:
        self.dismiss(event.option.id)

    def action_dismiss_screen(self) -> None:
        self.dismiss("")


class ActionPicker(ModalScreen[str]):
    """Small action menu after selecting an item."""

    BINDINGS = [
        Binding("escape", "dismiss_screen", "Close"),
    ]

    DEFAULT_CSS = """
    ActionPicker {
        align: center middle;
    }
    #action-container {
        width: 50;
        height: auto;
        max-height: 12;
        border: heavy $accent;
        background: $surface;
        padding: 1;
    }
    """

    def __init__(self, title: str, actions: list[tuple[str, str]]) -> None:
        super().__init__()
        self._title = title
        self._actions = actions

    def compose(self) -> ComposeResult:
        from textual.containers import Vertical
        from textual.widgets import OptionList
        from textual.widgets.option_list import Option

        with Vertical(id="action-container"):
            yield Static(f"[bold]{self._title}[/]")
            options = [Option(label, id=action_id) for action_id, label in self._actions]
            yield OptionList(*options)

    def on_option_list_option_selected(self, event) -> None:
        self.dismiss(event.option.id)

    def action_dismiss_screen(self) -> None:
        self.dismiss("")


class LLMCodeApp(App):
    """Textual TUI for llm-code — Claude Code style interface."""

    CSS = """
    #chat-log {
        height: 1fr;
        scrollbar-size: 1 1;
        padding: 0 1;
    }
    #prompt-input {
        dock: bottom;
        height: auto;
        max-height: 8;
        margin: 0 1;
    }
    """
    ALLOW_SELECT = True
    ENABLE_SELECT_AUTO_SCROLL = True

    BINDINGS = [
        Binding("ctrl+d", "quit", "Quit", priority=True),
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+v", "paste_image", "Paste Image", show=False, priority=True),
    ]

    def __init__(
        self,
        config: RuntimeConfig,
        cwd: Path | None = None,
        budget: int | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._cwd = cwd or Path.cwd()
        self._budget = budget
        self._runtime: ConversationRuntime | None = None
        self._tool_reg = ToolRegistry()
        self._session_manager = SessionManager(Path.home() / ".llm-code" / "sessions")
        self._text_buffer = ""
        self._output_tokens = 0
        self._is_running = False
        self._current_worker = None
        self._pending_images: list = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(id="chat-log", wrap=True, highlight=True, markup=True)
        from textual.suggester import SuggestFromList
        _cmds = [
            "/help", "/clear", "/model", "/skill", "/skill search", "/skill install",
            "/skill enable", "/skill disable", "/skill remove",
            "/mcp", "/mcp install", "/mcp remove", "/mcp search",
            "/plugin", "/plugin install", "/plugin enable", "/plugin disable", "/plugin remove",
            "/memory", "/memory get", "/memory set", "/memory delete",
            "/session list", "/session save", "/session switch",
            "/undo", "/undo list", "/index", "/index rebuild",
            "/image", "/cost", "/budget", "/cd", "/lsp", "/exit",
        ]
        yield Input(
            placeholder="Type a message... (/help for commands)",
            id="prompt-input",
            suggester=SuggestFromList(_cmds, case_sensitive=False),
        )
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"llm-code . {self._config.model or 'no model'}"
        self.sub_title = "Initializing..."
        self._render_welcome()
        self.query_one("#prompt-input", Input).focus()
        self._init_session_async()

    @work(thread=True)
    def _init_session_async(self) -> None:
        self._init_session()
        self.call_from_thread(self._update_status, "Ready")

    # ── Welcome Banner ──────────────────────────────────────────────

    def _render_welcome(self) -> None:
        log = self.query_one("#chat-log", RichLog)

        # Banner
        log.write(Text("  ╭──────────────╮", style="cyan"))
        log.write(Text("  │   llm-code   │", style="bold cyan"))
        log.write(Text("  ╰──────────────╯", style="cyan"))

        model = self._config.model or "(not set)"
        branch = _detect_git_branch(self._cwd)
        workspace = self._cwd.name
        if branch:
            workspace += f" \u00b7 {branch}"
        perm = self._config.permission_mode or "prompt"

        import sys
        paste_key = "Cmd+V" if sys.platform == "darwin" else "Ctrl+V"

        for label, value in [
            ("Model", model),
            ("Workspace", workspace),
            ("Directory", str(self._cwd)),
            ("Permissions", perm),
            ("Quick start", "/help \u00b7 /skill \u00b7 /mcp"),
            ("Multiline", "Shift+Enter inserts a newline"),
            ("Images", f"{paste_key} pastes from clipboard"),
        ]:
            log.write(Text.assemble(
                (f"  {label:<17}", "grey50"),
                (value, ""),
            ))
        log.write(Text(""))

    # ── Session Initialization ──────────────────────────────────────

    def _init_session(self) -> None:
        """Initialize the conversation runtime."""
        api_key = os.environ.get(self._config.provider_api_key_env, "")
        base_url = self._config.provider_base_url or ""

        provider = ProviderClient.from_model(
            model=self._config.model,
            base_url=base_url,
            api_key=api_key,
            timeout=self._config.timeout,
            max_retries=self._config.max_retries,
            native_tools=self._config.native_tools,
        )

        # Register core tools
        for tool in (
            ReadFileTool(),
            WriteFileTool(),
            EditFileTool(),
            BashTool(),
            GlobSearchTool(),
            GrepSearchTool(),
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

        # Checkpoint manager
        checkpoint_mgr = None
        if (self._cwd / ".git").is_dir():
            from llm_code.runtime.checkpoint import CheckpointManager
            checkpoint_mgr = CheckpointManager(self._cwd)

        # Token budget
        token_budget = None
        if self._budget is not None:
            from llm_code.runtime.token_budget import TokenBudget
            token_budget = TokenBudget(target=self._budget)

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
        )

        # Skills
        from llm_code.runtime.skills import SkillLoader
        from llm_code.marketplace.installer import PluginInstaller
        skill_dirs: list[Path] = [
            Path.home() / ".llm-code" / "skills",
            self._cwd / ".llm-code" / "skills",
        ]
        # Scan installed plugins for skills directories
        plugin_dir = Path.home() / ".llm-code" / "plugins"
        if plugin_dir.is_dir():
            pi = PluginInstaller(plugin_dir)
            for p in pi.list_installed():
                if p.enabled and p.manifest.skills:
                    sp = p.path / p.manifest.skills
                    if sp.is_dir():
                        skill_dirs.append(sp)
                # Also check direct skills/ subdirectory
                direct = p.path / "skills"
                if p.enabled and direct.is_dir() and direct not in skill_dirs:
                    skill_dirs.append(direct)
        self._skills = SkillLoader().load_from_dirs(skill_dirs)

        # Memory
        from llm_code.runtime.memory import MemoryStore
        memory_dir = Path.home() / ".llm-code" / "memory"
        self._memory = MemoryStore(memory_dir, self._cwd)

        # Register memory tools
        from llm_code.tools.memory_tools import MemoryStoreTool, MemoryRecallTool, MemoryListTool
        for tool_cls in (MemoryStoreTool, MemoryRecallTool, MemoryListTool):
            try:
                self._tool_reg.register(tool_cls(self._memory))
            except ValueError:
                pass

    # ── Display Helpers ─────────────────────────────────────────────

    @staticmethod
    def _tool_border(tool_name: str, detail_lines: list) -> list:
        """Create a bordered tool call box. detail_lines may be str or Text."""
        border_len = len(tool_name) + 4
        texts = []
        texts.append(Text.assemble(
            ("\u256d\u2500 ", "grey62"),
            (tool_name, "bold cyan"),
            (" \u2500\u256e", "grey62"),
        ))
        for line in detail_lines:
            if isinstance(line, Text):
                row = Text.assemble(("\u2502 ", "grey62"), line)
            else:
                row = Text.assemble(("\u2502 ", "grey62"), (str(line), ""))
            texts.append(row)
        texts.append(Text("\u2570" + "\u2500" * border_len + "\u256f", style="grey62"))
        return texts

    def _show_user_message(self, text: str) -> None:
        log = self.query_one("#chat-log", RichLog)
        log.write(Text(""))
        log.write(Text.assemble(("\u276f ", "bold white"), (text, "bold white")))
        log.write(Text(""))

    def _show_assistant_text(self, text: str) -> None:
        log = self.query_one("#chat-log", RichLog)
        from rich.markdown import Markdown
        log.write(Markdown(text, code_theme="monokai"))

    def _show_tool_start(self, tool_name: str, args_summary: str) -> None:
        import json
        log = self.query_one("#chat-log", RichLog)
        log.write(Text(""))

        try:
            args = json.loads(args_summary.replace("'", '"'))
        except Exception:
            args = {}
        if not isinstance(args, dict):
            args = {}

        if tool_name == "bash":
            cmd = args.get("command", args_summary)[:120]
            detail = Text.assemble(("$ ", "bold white"), (cmd, "white on color(236)"))
            lines = [detail]
        elif tool_name == "read_file":
            path = args.get("path", args_summary)
            lines = [Text(f"\U0001f4c4 Reading {path}\u2026")]
        elif tool_name == "write_file":
            path = args.get("path", args_summary)
            lines = [Text(f"\u270f\ufe0f  Writing {path}")]
        elif tool_name == "edit_file":
            path = args.get("path", args_summary)
            lines = [Text(f"\U0001f4dd Editing {path}")]
        elif tool_name in ("glob_search", "grep_search"):
            pattern = args.get("pattern", args.get("glob", args_summary))
            lines = [Text(f"\U0001f50e {pattern}")]
        elif tool_name == "agent":
            task = args.get("task", args_summary)[:80]
            lines = [Text(f"\U0001f916 {task}")]
        else:
            parts = tool_name.split("_")
            pretty = "".join(p.capitalize() for p in parts)
            vals = list(args.values())
            summary = str(vals[0])[:80] if vals else args_summary[:80]
            lines = [Text(f"{pretty}({summary})")]

        for t in self._tool_border(tool_name, lines):
            log.write(t)

    def _show_tool_result(self, tool_name: str, output: str, is_error: bool) -> None:
        log = self.query_one("#chat-log", RichLog)

        if is_error:
            msg = output.strip()[:200]
            log.write(Text.assemble(("\u2717 ", "bold red"), (msg, "red")))
        elif tool_name == "edit_file":
            raw_lines = output.strip().splitlines()[:12]
            wrote_any = False
            for line in raw_lines:
                if line.startswith("- "):
                    log.write(Text.assemble(("- ", "color(203)"), (line[2:], "color(203)")))
                    wrote_any = True
                elif line.startswith("+ "):
                    log.write(Text.assemble(("+ ", "color(70)"), (line[2:], "color(70)")))
                    wrote_any = True
                else:
                    log.write(Text(line[:150], style="grey50"))
            if not wrote_any:
                log.write(Text.assemble(("\u2713 ", "bold green"), (output.strip()[:150], "grey50")))
        elif tool_name in ("write_file", "git_commit"):
            log.write(Text.assemble(("\u2713 ", "bold green"), (output.strip()[:150], "grey50")))
        elif tool_name == "read_file":
            lines = output.strip().splitlines()
            preview = lines[0][:120] if lines else "(empty)"
            count = len(lines)
            summary = f"{preview}  \u2026 ({count} lines)" if count > 1 else preview
            log.write(Text.assemble(("\u2713 ", "bold green"), (summary, "grey50")))
            if count > _TRUNCATION_THRESHOLD:
                log.write(Text(
                    "\u2026 output truncated for display; full result preserved in session.",
                    style="grey50",
                ))
        elif tool_name == "bash":
            raw_lines = output.strip().splitlines()
            for line in raw_lines[:10]:
                log.write(Text(line[:150], style="grey50"))
            if len(raw_lines) > 10:
                log.write(Text(
                    "\u2026 output truncated for display; full result preserved in session.",
                    style="grey50",
                ))
            log.write(Text.assemble(("\u2713 ", "bold green"), ("done", "grey50")))
        else:
            raw_lines = output.strip().splitlines() if output.strip() else ["(no output)"]
            for line in raw_lines[:5]:
                log.write(Text(line[:150], style="grey50"))
            if len(raw_lines) > 5:
                log.write(Text(
                    "\u2026 output truncated for display; full result preserved in session.",
                    style="grey50",
                ))
            log.write(Text.assemble(("\u2713 ", "bold green"), ("done", "grey50")))

        log.write(Text(""))

    def _flush_text(self) -> None:
        """Flush accumulated text buffer as Markdown."""
        text = self._text_buffer.strip()
        if text:
            self._show_assistant_text(text)
        self._text_buffer = ""

    def _update_status(self, status: str) -> None:
        try:
            self.sub_title = status
        except Exception:
            pass

    # ── Input Handling ──────────────────────────────────────────────

    def on_paste(self, event) -> None:
        """Cmd+V / system paste: check clipboard for image before inserting text."""
        from llm_code.cli.image import capture_clipboard_image
        img = capture_clipboard_image()
        if img is not None:
            event.prevent_default()
            event.stop()
            self._pending_images.append(img)
            inp = self.query_one(Input)
            inp.value = inp.value + "[image pasted] "
            log = self.query_one("#chat-log", RichLog)
            log.write(Text("📎 Image from clipboard attached", style="dim"))

    def action_paste_image(self) -> None:
        """Ctrl+V: check clipboard for image."""
        from llm_code.cli.image import capture_clipboard_image
        img = capture_clipboard_image()
        if img is not None:
            self._pending_images.append(img)
            inp = self.query_one(Input)
            inp.value = inp.value + "[image pasted] "
            log = self.query_one("#chat-log", RichLog)
            log.write(Text("📎 Image from clipboard attached", style="dim"))
        else:
            # No image — let Textual handle normal paste
            inp = self.query_one(Input)
            inp.action_paste()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""

        if not text:
            return

        # Collect pending images
        images = list(self._pending_images)
        self._pending_images.clear()

        # Detect drag-and-dropped image paths
        from llm_code.cli.image import extract_dropped_images as _extract_dropped_images
        clean_text, dropped = _extract_dropped_images(text)
        images.extend(dropped)

        # Strip image paste marker
        clean_text = clean_text.replace("[image pasted]", "").strip()
        if not clean_text and images:
            clean_text = "What is in this image?"

        if clean_text.startswith("/"):
            self._handle_slash_command(clean_text)
        else:
            if images:
                log = self.query_one("#chat-log", RichLog)
                log.write(Text(f"📎 Sending with {len(images)} image(s)", style="dim"))
            self._run_turn(clean_text, images=images or None)

    def action_cancel(self) -> None:
        """Cancel the current operation."""
        if self._current_worker is not None and self._current_worker.is_running:
            self._current_worker.cancel()
            self._is_running = False
            self._update_status("Cancelled")
            log = self.query_one("#chat-log", RichLog)
            log.write(Text("(cancelled)", style="dim italic"))

    # ── Slash Commands ──────────────────────────────────────────────

    def _handle_slash_command(self, text: str) -> None:
        cmd = parse_slash_command(text)
        if cmd is None:
            return

        log = self.query_one("#chat-log", RichLog)
        name = cmd.name
        args = cmd.args.strip()

        if name in ("exit", "quit"):
            self.exit()

        elif name == "help":
            log.write(Text("Available commands:", style="bold"))
            for cmd, desc in [
                ("/help", "Show this help"),
                ("/clear", "Clear conversation"),
                ("/model <name>", "Switch model"),
                ("/skill", "Browse & manage skills"),
                ("/mcp", "Browse & manage MCP servers"),
                ("/plugin", "Browse & manage plugins"),
                ("/memory", "Project memory"),
                ("/session list|save|switch", "Manage sessions"),
                ("/undo", "Undo last file change"),
                ("/index", "Project index"),
                ("/image <path>", "Attach image"),
                ("/cost", "Token usage"),
                ("/budget <n>", "Set token budget"),
                ("/cd <dir>", "Change directory"),
                ("/lsp", "LSP server status"),
                ("/exit", "Quit"),
            ]:
                log.write(Text(f"  {cmd:<30s} {desc}", style="dim"))
            log.write(Text(""))

        elif name == "clear":
            log.clear()
            self._init_session()
            self._render_welcome()
            log.write(Text("Conversation cleared.", style="dim"))

        elif name == "model":
            if args:
                self._config = dataclasses.replace(self._config, model=args)
                self._init_session()
                self.title = f"llm-code . {args}"
                log.write(Text(f"Model switched to: {args}", style="dim"))
            else:
                log.write(
                    Text(f"Current model: {self._config.model or '(not set)'}", style="dim")
                )

        elif name == "cost":
            if self._runtime is not None and self._runtime.session:
                usage = self._runtime.session.total_usage
                log.write(Text(
                    f"Tokens  in: {usage.input_tokens:,}  out: {usage.output_tokens:,}",
                    style="dim",
                ))
            else:
                log.write(Text("No session active.", style="dim"))

        elif name == "cd":
            if args:
                new_path = Path(args).expanduser()
                if not new_path.is_absolute():
                    new_path = self._cwd / new_path
                if new_path.is_dir():
                    self._cwd = new_path
                    os.chdir(new_path)
                    log.write(Text(f"Working directory: {new_path}", style="dim"))
                else:
                    log.write(Text(f"Directory not found: {new_path}", style="red"))
            else:
                log.write(Text(f"Current directory: {self._cwd}", style="dim"))

        elif name == "budget":
            if args:
                try:
                    target = int(args)
                    self._budget = target
                    log.write(Text(f"Token budget set: {target:,}", style="dim"))
                except ValueError:
                    log.write(Text("Usage: /budget <number>", style="red"))
            else:
                if self._budget is not None:
                    log.write(Text(f"Current token budget: {self._budget:,}", style="dim"))
                else:
                    log.write(Text("No budget set.", style="dim"))

        elif name == "skill":
            self._tui_skill_command(args, log)

        elif name == "mcp":
            self._tui_mcp_command(args, log)

        elif name == "plugin":
            self._tui_plugin_command(args, log)

        elif name == "memory":
            self._tui_memory_command(args, log)

        elif name == "undo":
            if hasattr(self, '_checkpoint_mgr') and self._checkpoint_mgr:
                if args.strip() == "list":
                    for cp in self._checkpoint_mgr.list_checkpoints():
                        log.write(Text(f"  {cp.id}  {cp.tool_name}  {cp.timestamp[:19]}", style="dim"))
                elif self._checkpoint_mgr.can_undo():
                    cp = self._checkpoint_mgr.undo()
                    if cp:
                        log.write(Text(f"Undone: {cp.tool_name} ({cp.tool_args_summary[:50]})", style="green"))
                else:
                    log.write(Text("Nothing to undo.", style="dim"))
            else:
                log.write(Text("Not in a git repository — undo not available.", style="red"))

        elif name == "index":
            if args.strip() == "rebuild":
                from llm_code.runtime.indexer import ProjectIndexer
                idx = ProjectIndexer(self._cwd).build_index()
                log.write(Text(f"Index rebuilt: {len(idx.files)} files, {len(idx.symbols)} symbols", style="dim"))
            elif hasattr(self, '_project_index') and self._project_index:
                log.write(Text(f"Files: {len(self._project_index.files)}, Symbols: {len(self._project_index.symbols)}", style="dim"))
                for s in self._project_index.symbols[:20]:
                    log.write(Text(f"  {s.kind} {s.name} — {s.file}:{s.line}", style="dim"))
            else:
                log.write(Text("No index available.", style="dim"))

        elif name == "session":
            parts = args.split(None, 1)
            subcmd = parts[0] if parts else "list"
            subargs = parts[1] if len(parts) > 1 else ""
            if subcmd == "list":
                sessions = self._session_manager.list_sessions()
                if not sessions:
                    log.write(Text("No saved sessions.", style="dim"))
                for s in sessions:
                    log.write(Text(f"  {s.id}  {s.project_path}  ({s.message_count} msgs)", style="dim"))
            elif subcmd == "save" and self._runtime:
                path = self._session_manager.save(self._runtime.session)
                log.write(Text(f"Session saved: {path}", style="dim"))

        elif name == "image":
            if args:
                from llm_code.cli.image import load_image_from_path
                try:
                    img = load_image_from_path(args)
                    self._pending_images.append(img)
                    log.write(Text(f"📎 Image attached: {args}", style="dim"))
                except FileNotFoundError:
                    log.write(Text(f"Image not found: {args}", style="red"))
            else:
                log.write(Text("Usage: /image <path>", style="red"))

        elif name == "lsp":
            log.write(Text("LSP: not started in this session.", style="dim"))

        else:
            log.write(Text(f"Unknown command: /{name} -- type /help for help", style="red"))

    # ── NPM Fetchers ───────────────────────────────────────────────

    async def _fetch_npm_skills(self) -> list[tuple[str, str]]:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://registry.npmjs.org/-/v1/search",
                params={"text": "claude-code skill", "size": 50},
            )
            resp.raise_for_status()
            data = resp.json()
        results = []
        for obj in data.get("objects", []):
            pkg = obj.get("package", {})
            name = pkg.get("name", "")
            desc = pkg.get("description", "")[:70]
            if "skill" in name.lower() or ("claude" in name.lower() and "skill" in desc.lower()):
                results.append((name, desc))
        return results

    async def _fetch_npm_mcp(self) -> list[tuple[str, str]]:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://registry.npmjs.org/-/v1/search",
                params={"text": "mcp server modelcontextprotocol", "size": 50},
            )
            resp.raise_for_status()
            data = resp.json()
        results = []
        for obj in data.get("objects", []):
            pkg = obj.get("package", {})
            name = pkg.get("name", "")
            desc = pkg.get("description", "")[:70]
            if "mcp" in name.lower() or "modelcontextprotocol" in name.lower():
                results.append((name, desc))
        return results

    # ── Marketplace Handlers ─────────────────────────────────────────

    def _tui_skill_command(self, args: str, log: RichLog) -> None:
        parts = args.strip().split(None, 1)
        subcmd = parts[0] if parts else ""
        subargs = parts[1] if len(parts) > 1 else ""

        if subcmd == "install" and subargs:
            source = subargs.strip()
            import subprocess as _sp
            if "/" in source and not source.startswith("@"):
                # GitHub repo — clone skills/ subdirectory
                repo = source.replace("https://github.com/", "").rstrip("/")
                name = repo.split("/")[-1]
                dest = Path.home() / ".llm-code" / "skills" / name
                if dest.exists():
                    import shutil
                    shutil.rmtree(dest)
                # Clone full repo to temp, then copy skills/
                import tempfile
                with tempfile.TemporaryDirectory() as tmp:
                    log.write(Text(f"⠋ Cloning {repo}...", style="blue"))
                    result = _sp.run(
                        ["git", "clone", "--depth", "1", f"https://github.com/{repo}.git", tmp],
                        capture_output=True, text=True, timeout=30,
                    )
                    if result.returncode == 0:
                        skills_src = Path(tmp) / "skills"
                        if skills_src.is_dir():
                            import shutil
                            shutil.copytree(skills_src, dest)
                            log.write(Text(f"✓ Installed skills from {repo}", style="green"))
                        else:
                            # No skills/ dir — copy entire repo as a skill
                            import shutil
                            shutil.copytree(tmp, dest)
                            log.write(Text(f"✓ Installed {name}", style="green"))
                        log.write(Text("  Restart llm-code to activate.", style="dim"))
                    else:
                        log.write(Text(f"✗ Failed: {result.stderr[:200]}", style="red"))
            else:
                # npm package
                skill_dir = Path.home() / ".llm-code" / "skills" / source.split("/")[-1].replace("@", "")
                skill_dir.mkdir(parents=True, exist_ok=True)
                result = _sp.run(["npm", "pack", source, "--pack-destination", str(skill_dir)], capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    import glob as _glob
                    tarballs = _glob.glob(str(skill_dir / "*.tgz"))
                    if tarballs:
                        _sp.run(["tar", "xzf", tarballs[0], "-C", str(skill_dir), "--strip-components=1"], capture_output=True, timeout=10)
                        Path(tarballs[0]).unlink(missing_ok=True)
                    log.write(Text(f"✓ Installed to {skill_dir}", style="green"))
                else:
                    log.write(Text(f"✗ Failed: {result.stderr[:200]}", style="red"))
            return

        if subcmd == "enable" and subargs:
            marker = Path.home() / ".llm-code" / "skills" / subargs / ".disabled"
            marker.unlink(missing_ok=True)
            log.write(Text(f"✓ Enabled {subargs}", style="green"))
        elif subcmd == "disable" and subargs:
            marker = Path.home() / ".llm-code" / "skills" / subargs / ".disabled"
            marker.touch()
            log.write(Text(f"Disabled {subargs}", style="dim"))
        elif subcmd == "remove" and subargs:
            import shutil
            d = Path.home() / ".llm-code" / "skills" / subargs
            if d.is_dir():
                shutil.rmtree(d)
                log.write(Text(f"✓ Removed {subargs}", style="green"))
            else:
                log.write(Text(f"Not found: {subargs}", style="red"))
        else:
            # Interactive skill browser
            all_skills = []
            if self._skills:
                all_skills = list(self._skills.auto_skills) + list(self._skills.command_skills)

            items: list[tuple[str, str, bool]] = []
            for s in all_skills:
                tokens = len(s.content) // 4
                mode = "auto" if s.auto else f"/{s.trigger}"
                items.append((s.name, f"{mode} · ~{tokens} tokens", True))

            # Fetch marketplace skills from npm
            import asyncio as _aio
            try:
                try:
                    _aio.get_running_loop()
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        market = pool.submit(_aio.run, self._fetch_npm_skills()).result()
                except RuntimeError:
                    market = _aio.run(self._fetch_npm_skills())
            except Exception:
                market = []

            installed_names = {s.name for s in all_skills}
            for name, desc in market:
                if name not in installed_names:
                    items.append((name, desc, False))

            def on_selected(selected: str) -> None:
                if not selected:
                    return
                # Show action picker
                is_installed = any(s.name == selected for s in all_skills)
                actions = []
                if is_installed:
                    actions.append(("enable", f"Enable {selected}"))
                    actions.append(("disable", f"Disable {selected}"))
                    actions.append(("remove", f"Remove {selected}"))
                else:
                    actions.append(("install", f"Install {selected}"))

                def on_action(action: str) -> None:
                    if not action:
                        return
                    if action == "enable":
                        self._tui_skill_command(f"enable {selected}", log)
                    elif action == "disable":
                        self._tui_skill_command(f"disable {selected}", log)
                    elif action == "remove":
                        self._tui_skill_command(f"remove {selected}", log)
                    elif action == "install":
                        self._tui_skill_command(f"install {selected}", log)

                self.push_screen(
                    ActionPicker(f"Action for {selected}", actions),
                    on_action,
                )

            self.push_screen(
                MarketplaceBrowser(
                    title="Skills",
                    items=items,
                    actions="Enter: select · Esc: close",
                ),
                on_selected,
            )

    def _tui_mcp_command(self, args: str, log: RichLog) -> None:
        parts = args.strip().split(None, 1)
        subcmd = parts[0] if parts else ""
        subargs = parts[1] if len(parts) > 1 else ""

        if not subcmd:
            # Interactive MCP browser with marketplace
            servers = self._config.mcp_servers
            items = []
            for name, cfg in servers.items():
                cmd = cfg.get("command", "")
                srv_args = " ".join(cfg.get("args", []))
                items.append((name, f"{cmd} {srv_args}", True))

            # Fetch marketplace
            import asyncio as _aio
            try:
                try:
                    _aio.get_running_loop()
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        market = pool.submit(_aio.run, self._fetch_npm_mcp()).result()
                except RuntimeError:
                    market = _aio.run(self._fetch_npm_mcp())
            except Exception:
                market = []

            for name, desc in market:
                if name not in servers:
                    items.append((name, desc, False))

            def on_selected(selected: str) -> None:
                if not selected:
                    return
                is_configured = selected in servers
                actions = []
                if is_configured:
                    actions.append(("remove", f"Remove {selected}"))
                else:
                    actions.append(("install", f"Install {selected}"))

                def on_action(action: str) -> None:
                    if action == "install":
                        self._tui_mcp_command(f"install {selected}", log)
                    elif action == "remove":
                        self._tui_mcp_command(f"remove {selected}", log)

                self.push_screen(ActionPicker(f"Action for {selected}", actions), on_action)

            self.push_screen(
                MarketplaceBrowser(title="MCP Servers", items=items, actions="Enter: select · Esc: close"),
                on_selected,
            )
            return

        if subcmd == "install" and subargs:
            import json
            package = subargs.strip()
            server_name = package.split("/")[-1].replace("@", "").replace("server-", "")
            config_path = Path.home() / ".llm-code" / "config.json"
            config = {}
            if config_path.exists():
                try:
                    config = json.loads(config_path.read_text())
                except Exception:
                    pass
            config.setdefault("mcpServers", {})[server_name] = {"command": "npx", "args": ["-y", package]}
            config_path.write_text(json.dumps(config, indent=2))
            log.write(Text(f"✓ Added {server_name} to config. Restart to activate.", style="green"))
        elif subcmd == "remove" and subargs:
            import json
            config_path = Path.home() / ".llm-code" / "config.json"
            if config_path.exists():
                config = json.loads(config_path.read_text())
                if subargs in config.get("mcpServers", {}):
                    del config["mcpServers"][subargs]
                    config_path.write_text(json.dumps(config, indent=2))
                    log.write(Text(f"✓ Removed {subargs}", style="green"))
                else:
                    log.write(Text(f"Not found: {subargs}", style="red"))
        else:
            servers = self._config.mcp_servers
            log.write(Text(f"MCP Servers ({len(servers)} configured)", style="bold"))
            for name, cfg in servers.items():
                cmd = cfg.get("command", "")
                srv_args = " ".join(cfg.get("args", []))
                log.write(Text(f"  ● {name} · {cmd} {srv_args}", style="green"))
            if not servers:
                log.write(Text("  No MCP servers configured.", style="dim"))
            log.write(Text("  /mcp install <npm-package>  /mcp remove <name>", style="dim"))

    def _tui_plugin_command(self, args: str, log: RichLog) -> None:
        from llm_code.marketplace.installer import PluginInstaller
        installer = PluginInstaller(Path.home() / ".llm-code" / "plugins")
        parts = args.strip().split(None, 1)
        subcmd = parts[0] if parts else ""
        subargs = parts[1] if len(parts) > 1 else ""

        if not subcmd:
            # Interactive plugin browser — installed + built-in registry
            installed = installer.list_installed()
            installed_names = {p.manifest.name for p in installed}
            items = []
            for p in installed:
                status = "enabled" if p.enabled else "disabled"
                items.append((p.manifest.name, f"v{p.manifest.version} · {status}", True))

            # Add all known plugins from built-in registry
            from llm_code.marketplace.builtin_registry import get_all_known_plugins
            for p in get_all_known_plugins():
                if p["name"] not in installed_names:
                    skill_info = f"{p['skills']} skills · " if p["skills"] > 0 else ""
                    source_tag = "official" if p["source"] == "official" else "community"
                    items.append((p["name"], f"{skill_info}{p['desc']} [{source_tag}]", False))

            def on_selected(selected: str) -> None:
                if not selected:
                    return
                is_installed = selected in installed_names
                actions = []
                if is_installed:
                    actions.append(("enable", f"Enable {selected}"))
                    actions.append(("disable", f"Disable {selected}"))
                    actions.append(("remove", f"Remove {selected}"))
                else:
                    # Find repo from registry
                    from llm_code.marketplace.builtin_registry import get_all_known_plugins
                    registry = {p["name"]: p for p in get_all_known_plugins()}
                    repo = registry.get(selected, {}).get("repo", "")
                    if repo:
                        actions.append(("install", f"Install {selected} (from {repo})"))
                    else:
                        actions.append(("install", f"Install {selected}"))

                def on_action(action: str) -> None:
                    if not action:
                        return
                    if action == "install":
                        from llm_code.marketplace.builtin_registry import get_all_known_plugins
                        registry = {p["name"]: p for p in get_all_known_plugins()}
                        repo = registry.get(selected, {}).get("repo", "")
                        if repo:
                            self._tui_plugin_command(f"install {repo}", log)
                        else:
                            log.write(Text(f"No install source for {selected}. Try: /plugin install owner/repo", style="dim"))

                    elif action in ("enable", "disable", "remove"):
                        self._tui_plugin_command(f"{action} {selected}", log)

                self.push_screen(ActionPicker(f"Action for {selected}", actions), on_action)

            self.push_screen(
                MarketplaceBrowser(title="Plugins", items=items, actions="Enter: select · Esc: close"),
                on_selected,
            )
            return

        if subcmd == "install" and subargs:
            source = subargs.strip()
            # Detect GitHub repo (owner/repo or https://github.com/...)
            import subprocess as _sp
            if "/" in source and not source.startswith("@"):
                # GitHub repo
                repo = source.replace("https://github.com/", "").rstrip("/")
                name = repo.split("/")[-1]
                dest = Path.home() / ".llm-code" / "plugins" / name
                if dest.exists():
                    import shutil
                    shutil.rmtree(dest)
                log.write(Text(f"⠋ Cloning {repo}...", style="blue"))
                result = _sp.run(
                    ["git", "clone", "--depth", "1", f"https://github.com/{repo}.git", str(dest)],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode == 0:
                    installer.enable(name)
                    log.write(Text(f"✓ Installed {name} from GitHub", style="green"))
                    log.write(Text(f"  {dest}", style="dim"))
                    log.write(Text("  Restart llm-code to activate skills & hooks.", style="dim"))
                else:
                    log.write(Text(f"✗ Failed: {result.stderr[:200]}", style="red"))
            else:
                log.write(Text(f"Usage: /plugin install owner/repo", style="red"))
            return

        if subcmd == "enable" and subargs:
            installer.enable(subargs)
            log.write(Text(f"✓ Enabled {subargs}", style="green"))
        elif subcmd == "disable" and subargs:
            installer.disable(subargs)
            log.write(Text(f"Disabled {subargs}", style="dim"))
        elif subcmd in ("remove", "uninstall") and subargs:
            installer.uninstall(subargs)
            log.write(Text(f"✓ Removed {subargs}", style="green"))
        else:
            installed = installer.list_installed()
            log.write(Text(f"Plugins ({len(installed)} installed)", style="bold"))
            for p in installed:
                status = "●" if p.enabled else "○"
                log.write(Text(f"  {status} {p.manifest.name} v{p.manifest.version}", style="green" if p.enabled else "red"))
            if not installed:
                log.write(Text("  No plugins installed.", style="dim"))

    def _tui_memory_command(self, args: str, log: RichLog) -> None:
        if not hasattr(self, '_memory') or not self._memory:
            log.write(Text("Memory not initialized.", style="red"))
            return
        parts = args.strip().split(None, 2)
        subcmd = parts[0] if parts else ""

        if subcmd == "set" and len(parts) > 2:
            self._memory.store(parts[1], parts[2])
            log.write(Text(f"Stored: {parts[1]}", style="dim"))
        elif subcmd == "get" and len(parts) > 1:
            val = self._memory.recall(parts[1])
            log.write(Text(val or f"Key not found: {parts[1]}", style="dim" if val else "red"))
        elif subcmd == "delete" and len(parts) > 1:
            self._memory.delete(parts[1])
            log.write(Text(f"Deleted: {parts[1]}", style="dim"))
        else:
            entries = self._memory.get_all()
            log.write(Text(f"Memory ({len(entries)} entries)", style="bold"))
            for k, v in entries.items():
                log.write(Text(f"  {k}: {v.value[:60]}", style="dim"))
            if not entries:
                log.write(Text("  No memories stored.", style="dim"))

    # ── Streaming Turn ──────────────────────────────────────────────

    @work(exclusive=True)
    async def _run_turn(self, user_input: str, images: list | None = None) -> None:
        self._is_running = True
        self._text_buffer = ""
        self._output_tokens = 0

        self._show_user_message(user_input)
        self._update_status("Thinking...")
        log = self.query_one("#chat-log", RichLog)
        log.write(Text.assemble(
            (_SPINNER_FRAMES[0] + " ", "blue"),
            ("Thinking\u2026", "blue"),
        ))

        start = time.monotonic()
        first_token = False

        # Tag filter state: hide <tool_call> and <think> blocks
        in_tool_call_tag = False
        in_think_tag = False
        tool_tag_buffer = ""

        if self._runtime is None:
            self._init_session()

        assert self._runtime is not None

        try:
            async for event in self._runtime.run_turn(user_input, images=images):
                if isinstance(event, StreamTextDelta):
                    self._output_tokens += len(event.text) // 4

                    if not first_token:
                        first_token = True
                        elapsed = time.monotonic() - start
                        self._update_status(
                            f"Streaming... ({elapsed:.1f}s to first token)"
                        )

                    # Filter <tool_call>...</tool_call> and <think>...</think> tags
                    for char in event.text:
                        if in_tool_call_tag:
                            tool_tag_buffer += char
                            if tool_tag_buffer.endswith("</tool_call>"):
                                in_tool_call_tag = False
                                tool_tag_buffer = ""
                        elif in_think_tag:
                            tool_tag_buffer += char
                            if tool_tag_buffer.endswith("</think>"):
                                in_think_tag = False
                                tool_tag_buffer = ""
                        elif tool_tag_buffer:
                            tool_tag_buffer += char
                            if tool_tag_buffer == "<tool_call>":
                                in_tool_call_tag = True
                            elif tool_tag_buffer == "<think>":
                                in_think_tag = True
                            elif (
                                not "<tool_call>".startswith(tool_tag_buffer)
                                and not "<think>".startswith(tool_tag_buffer)
                            ):
                                self._text_buffer += tool_tag_buffer
                                tool_tag_buffer = ""
                        elif char == "<":
                            tool_tag_buffer = "<"
                        else:
                            self._text_buffer += char
                            # Flush on paragraph boundaries, but NOT inside code blocks
                            in_code = self._text_buffer.count("```") % 2 == 1
                            if not in_code and (
                                self._text_buffer.endswith("\n\n")
                                or self._text_buffer.endswith("\u3002")
                            ):
                                self._flush_text()

                elif isinstance(event, StreamToolExecStart):
                    if not first_token:
                        first_token = True
                    self._flush_text()
                    self._show_tool_start(event.tool_name, event.args_summary)
                    self._update_status(f"Running {event.tool_name}...")

                elif isinstance(event, StreamToolExecResult):
                    self._show_tool_result(
                        event.tool_name, event.output, event.is_error
                    )
                    self._update_status("Streaming...")

                elif isinstance(event, StreamToolProgress):
                    self._update_status(
                        f"{event.tool_name}: {event.message}"
                    )

                elif isinstance(event, StreamMessageStop):
                    self._flush_text()
                    if event.usage and (
                        event.usage.input_tokens > 0
                        or event.usage.output_tokens > 0
                    ):
                        self._output_tokens = event.usage.output_tokens

        except asyncio.CancelledError:
            self._flush_text()
            self._update_status("Cancelled")
            self._is_running = False
            return
        except Exception as exc:
            log = self.query_one("#chat-log", RichLog)
            log.write(Text(f"Error: {exc}", style="bold red"))
            self._update_status("Error")
            self._is_running = False
            return

        # Flush remaining text
        if tool_tag_buffer and not in_tool_call_tag:
            self._text_buffer += tool_tag_buffer
        self._flush_text()

        # Turn summary
        elapsed = time.monotonic() - start
        time_str = f"{elapsed:.1f}s" if elapsed < 60 else f"{elapsed / 60:.1f}m"
        log = self.query_one("#chat-log", RichLog)
        tokens_str = f"  \u2193{self._output_tokens:,} tok" if self._output_tokens > 0 else ""
        log.write(Text.assemble(
            ("\u2713 ", "bold green"),
            (f"Done ({time_str})", "green"),
            (tokens_str, "dim"),
        ))
        log.write(Text(""))

        self._update_status("Ready")
        self._is_running = False
