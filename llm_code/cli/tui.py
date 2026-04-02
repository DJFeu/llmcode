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


class LLMCodeApp(App):
    """Textual TUI for llm-code — Claude Code style interface."""

    CSS = """
    #chat-log {
        height: 1fr;
        scrollbar-size: 1;
        padding: 0 1;
    }
    #prompt-input {
        dock: bottom;
        height: auto;
        max-height: 8;
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+d", "quit", "Quit", priority=True),
        Binding("escape", "cancel", "Cancel", show=True),
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
        self._registry = ToolRegistry()
        self._session_manager = SessionManager(Path.home() / ".llm-code" / "sessions")
        self._text_buffer = ""
        self._output_tokens = 0
        self._is_running = False
        self._current_worker = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(id="chat-log", wrap=True, highlight=True, markup=True)
        yield Input(placeholder="Type a message... (/help for commands)", id="prompt-input")
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"llm-code . {self._config.model or 'no model'}"
        self.sub_title = "Ready"
        self._render_welcome()
        self._init_session()
        self.query_one("#prompt-input", Input).focus()

    # ── Welcome Banner ──────────────────────────────────────────────

    def _render_welcome(self) -> None:
        log = self.query_one("#chat-log", RichLog)
        bar = "\u2500" * 40
        log.write(Text(f"\u256d{bar}\u256e", style="cyan"))
        log.write(Text(f"\u2502  llm-code{' ' * 31}\u2502", style="bold cyan"))
        log.write(Text(f"\u2570{bar}\u256f", style="cyan"))

        model = self._config.model or "(not set)"
        branch = _detect_git_branch(self._cwd)
        workspace = f"{self._cwd.name}"
        if branch:
            workspace += f" . {branch}"
        perm = self._config.permission_mode or "prompt"

        log.write(Text(f"  Model          {model}", style="dim"))
        log.write(Text(f"  Workspace      {workspace}", style="dim"))
        log.write(Text(f"  Directory      {self._cwd}", style="dim"))
        log.write(Text(f"  Permissions    {perm}", style="dim"))
        log.write(Text("  Quick start    /help . /skill . /mcp", style="dim"))
        log.write(Text("  Multiline      Shift+Enter inserts a newline", style="dim"))
        log.write(Text("  Images         Ctrl+V pastes from clipboard", style="dim"))
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
                self._registry.register(tool)
            except ValueError:
                pass

        for cls in (
            GitStatusTool, GitDiffTool, GitLogTool, GitCommitTool,
            GitPushTool, GitStashTool, GitBranchTool,
        ):
            try:
                self._registry.register(cls())
            except ValueError:
                pass

        # Try to register AgentTool
        try:
            from llm_code.tools.agent import AgentTool
            if self._registry.get("agent") is None:
                self._registry.register(AgentTool(
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
            tool_registry=self._registry,
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
        skill_dirs = [
            Path.home() / ".llm-code" / "skills",
            self._cwd / ".llm-code" / "skills",
        ]
        self._skills = SkillLoader().load_from_dirs(skill_dirs)

        # Memory
        from llm_code.runtime.memory import MemoryStore
        memory_dir = Path.home() / ".llm-code" / "memory"
        self._memory = MemoryStore(memory_dir, self._cwd)

        # Register memory tools
        from llm_code.tools.memory_tools import MemoryStoreTool, MemoryRecallTool, MemoryListTool
        for tool_cls in (MemoryStoreTool, MemoryRecallTool, MemoryListTool):
            try:
                self._registry.register(tool_cls(self._memory))
            except ValueError:
                pass

    # ── Display Helpers ─────────────────────────────────────────────

    def _show_user_message(self, text: str) -> None:
        log = self.query_one("#chat-log", RichLog)
        log.write(Text(f"\u276f {text}", style="bold"))
        log.write(Text(""))

    def _show_assistant_text(self, text: str) -> None:
        log = self.query_one("#chat-log", RichLog)
        from rich.markdown import Markdown
        log.write(Markdown(text))

    def _show_tool_start(self, tool_name: str, args_summary: str) -> None:
        log = self.query_one("#chat-log", RichLog)
        display = _format_tool_call(tool_name, args_summary)
        log.write(Text(f"\u25cf {display}", style="bold blue"))

    def _show_tool_result(self, tool_name: str, output: str, is_error: bool) -> None:
        log = self.query_one("#chat-log", RichLog)

        if is_error:
            log.write(Text(f"  \u2514 {output.strip()[:150]}", style="red"))
        elif tool_name == "edit_file":
            for line in output.strip().splitlines()[:8]:
                if line.startswith("- "):
                    log.write(Text(f"  \u2514 {line}", style="red"))
                elif line.startswith("+ "):
                    log.write(Text(f"  \u2514 {line}", style="green"))
                else:
                    log.write(Text(f"  \u2514 {line[:150]}", style="dim"))
        elif tool_name in ("write_file", "git_commit"):
            log.write(Text(f"  \u2514 {output.strip()[:150]}", style="green"))
        elif tool_name == "read_file":
            lines = output.strip().splitlines()
            preview = lines[0][:120] if lines else "(empty)"
            count = len(lines)
            log.write(Text(f"  \u2514 {preview}", style="dim"))
            if count > 1:
                log.write(Text(f"    ... ({count} lines)", style="dim"))
        elif tool_name == "bash":
            lines = output.strip().splitlines()
            for line in lines[:8]:
                log.write(Text(f"  \u2514 {line[:150]}", style="dim"))
            if len(lines) > 8:
                log.write(Text(f"    ... ({len(lines)} lines)", style="dim"))
        else:
            lines = (
                output.strip().splitlines() if output.strip() else ["(no output)"]
            )
            for line in lines[:3]:
                log.write(Text(f"  \u2514 {line[:150]}", style="dim"))
            if len(lines) > 3:
                log.write(Text(f"    ... ({len(lines)} lines)", style="dim"))

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

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""

        if not text:
            return

        if text.startswith("/"):
            self._handle_slash_command(text)
        else:
            self._run_turn(text)

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
            log.write(Text("  /help        Show this help", style="dim"))
            log.write(Text("  /clear       Clear conversation", style="dim"))
            log.write(Text("  /model <n>   Switch model", style="dim"))
            log.write(Text("  /cost        Show token usage", style="dim"))
            log.write(Text("  /cd <dir>    Change directory", style="dim"))
            log.write(Text("  /budget <n>  Set token budget", style="dim"))
            log.write(Text("  /exit        Quit", style="dim"))
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

        else:
            log.write(Text(f"Unknown command: /{name} -- type /help for help", style="red"))

    # ── Streaming Turn ──────────────────────────────────────────────

    @work(exclusive=True)
    async def _run_turn(self, user_input: str, images: list | None = None) -> None:
        self._is_running = True
        self._text_buffer = ""
        self._output_tokens = 0

        self._show_user_message(user_input)
        self._update_status("Thinking...")

        start = time.monotonic()
        first_token = False

        # tool_call tag filter state
        in_tool_call_tag = False
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

                    # Filter <tool_call>...</tool_call> tags
                    for char in event.text:
                        if in_tool_call_tag:
                            tool_tag_buffer += char
                            if tool_tag_buffer.endswith("</tool_call>"):
                                in_tool_call_tag = False
                                tool_tag_buffer = ""
                        elif tool_tag_buffer:
                            tool_tag_buffer += char
                            if tool_tag_buffer == "<tool_call>":
                                in_tool_call_tag = True
                            elif not "<tool_call>".startswith(tool_tag_buffer):
                                self._text_buffer += tool_tag_buffer
                                tool_tag_buffer = ""
                        elif char == "<":
                            tool_tag_buffer = "<"
                        else:
                            self._text_buffer += char

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
        if elapsed > 0.5:
            tokens_str = (
                f"\u2193 {self._output_tokens:,} tokens"
                if self._output_tokens > 0
                else ""
            )
            time_str = (
                f"{elapsed:.1f}s" if elapsed < 60 else f"{elapsed / 60:.1f}m"
            )
            parts = [p for p in [time_str, tokens_str] if p]
            log = self.query_one("#chat-log", RichLog)
            log.write(Text(f"({' . '.join(parts)})", style="dim"))
            log.write(Text(""))

        self._update_status("Ready")
        self._is_running = False
