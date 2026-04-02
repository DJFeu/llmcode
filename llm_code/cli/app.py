"""Main CLI application entry point."""
from __future__ import annotations

import asyncio
import atexit
import os
import sys
from pathlib import Path

import click
from rich.console import Console

from llm_code.api.client import ProviderClient
from llm_code.cli.commands import SlashCommand, parse_slash_command
from llm_code.cli.image import load_image_from_path
from llm_code.cli.input import InputHandler
from llm_code.cli.render import TerminalRenderer
from llm_code.logging import get_logger, setup_logging
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

_BANNER = """[bold cyan]llm-code[/bold cyan] — AI coding assistant
Type [bold]/help[/bold] for commands, [bold]/exit[/bold] to quit.
"""

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}


def _format_tool_call(tool_name: str, args_summary: str) -> str:
    """Format tool call like Claude Code: Read(path) / Bash(cmd) / Edit(path)."""
    import json
    try:
        args = json.loads(args_summary.replace("'", '"'))
    except Exception:
        args = {}

    if not isinstance(args, dict):
        return f"{tool_name}({args_summary[:60]})"

    # Pick the most informative arg for display
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

    # CamelCase the tool name for display
    parts = tool_name.split("_")
    pretty_name = "".join(p.capitalize() for p in parts)
    return f"{pretty_name}({display})"


def _extract_dropped_images(text: str) -> tuple[str, list]:
    r"""Detect drag-and-dropped image file paths in user input.

    Terminal drag-and-drop produces paths like:
      /Users/adam/screenshot.png
      '/Users/adam/my screenshot.png'
      /Users/adam/my\ screenshot.png

    Returns (cleaned_text, list_of_ImageBlocks).
    """
    import re
    import shlex
    from pathlib import Path as P

    from llm_code.cli.image import load_image_from_path

    images = []
    remaining_parts = []

    # Split input by whitespace, but respect quotes and backslash-escapes
    try:
        tokens = shlex.split(text)
    except ValueError:
        # Unmatched quotes — fall back to simple split
        tokens = text.split()

    for token in tokens:
        # Strip trailing whitespace/newlines that drag-drop sometimes adds
        token = token.strip()
        if not token:
            continue

        path = P(token)
        if path.suffix.lower() in _IMAGE_EXTENSIONS and path.is_file():
            try:
                img = load_image_from_path(str(path))
                images.append(img)
            except Exception:
                remaining_parts.append(token)
        else:
            remaining_parts.append(token)

    cleaned = " ".join(remaining_parts)
    return cleaned, images

_PERMISSION_CHOICES = ["prompt", "auto_accept", "read_only", "workspace_write", "full_access"]

logger = get_logger(__name__)


@click.command()
@click.argument("prompt", required=False)
@click.option("--model", "-m", default=None, help="Model name to use")
@click.option("--api", default=None, help="API base URL")
@click.option("--api-key", default=None, help="API key (or set LLM_API_KEY env var)")
@click.option(
    "--permission",
    type=click.Choice(_PERMISSION_CHOICES),
    default=None,
    help="Permission mode",
)
@click.option("--budget", type=int, default=None, help="Token budget target")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def main(
    prompt: str | None,
    model: str | None,
    api: str | None,
    api_key: str | None,
    permission: str | None,
    budget: int | None,
    verbose: bool = False,
) -> None:
    """llm-code: AI coding assistant CLI."""
    setup_logging(verbose=verbose)
    cwd = Path.cwd()

    # Build CLI overrides dict
    cli_overrides: dict = {}
    if model:
        cli_overrides["model"] = model
    if api:
        cli_overrides.setdefault("provider", {})["base_url"] = api
    if api_key:
        os.environ["LLM_API_KEY"] = api_key
    if permission:
        cli_overrides.setdefault("permissions", {})["mode"] = permission

    # Load config
    user_dir = Path.home() / ".llm-code"
    config = load_config(
        user_dir=user_dir,
        project_dir=cwd,
        local_path=cwd / ".llm-code" / "config.json",
        cli_overrides=cli_overrides,
    )

    app = CliApp(config=config, cwd=cwd, budget=budget)

    # Detect stdin pipe mode
    stdin_is_pipe = not sys.stdin.isatty()

    if prompt:
        # One-shot mode: prompt provided as argument
        if stdin_is_pipe:
            # Prepend stdin content to prompt
            stdin_content = sys.stdin.read()
            full_prompt = f"{stdin_content}\n\n{prompt}" if stdin_content.strip() else prompt
        else:
            full_prompt = prompt
        asyncio.run(app.run_prompt(full_prompt))
    elif stdin_is_pipe:
        # Pipe mode without explicit prompt: read stdin as the prompt
        stdin_content = sys.stdin.read()
        if stdin_content.strip():
            asyncio.run(app.run_prompt(stdin_content))
    else:
        # Interactive REPL mode
        asyncio.run(app.run_repl())


class CliApp:
    """Main CLI application class."""

    def __init__(self, config: RuntimeConfig, cwd: Path | None = None, budget: int | None = None) -> None:
        self._config = config
        self._cwd = cwd or Path.cwd()
        self._budget = budget
        self._console = Console()
        self._renderer = TerminalRenderer(self._console)
        self._input = InputHandler(
            history_path=Path.home() / ".llm-code" / "history"
        )
        self._cleanup_registered = False
        self._pending_images: list = []  # Images queued for next turn

        # Build tool registry with all tools
        self._registry = ToolRegistry()
        self._registry.register(ReadFileTool())
        self._registry.register(WriteFileTool())
        self._registry.register(EditFileTool())
        self._registry.register(BashTool())
        self._registry.register(GlobSearchTool())
        self._registry.register(GrepSearchTool())
        for cls in (
            GitStatusTool, GitDiffTool, GitLogTool, GitCommitTool,
            GitPushTool, GitStashTool, GitBranchTool,
        ):
            self._registry.register(cls())

        # Try to register AgentTool if available
        try:
            from llm_code.tools.agent import AgentTool  # noqa: F401
        except ImportError:
            pass

        self._checkpoint_mgr = None
        self._memory = None
        self._project_index = None
        self._lsp_manager = None

        # Session manager
        session_dir = Path.home() / ".llm-code" / "sessions"
        self._session_manager = SessionManager(session_dir)

        # Runtime will be initialized on first use
        self._runtime: ConversationRuntime | None = None
        self._mcp_manager = None
        self._skills = None

    def _register_cleanup(self) -> None:
        """Register an atexit handler to gracefully shut down MCP/LSP servers."""
        if self._cleanup_registered:
            return
        self._cleanup_registered = True

        app_ref = self

        async def _async_cleanup() -> None:
            logger.info("Cleaning up resources on exit...")
            if app_ref._mcp_manager is not None:
                try:
                    await app_ref._mcp_manager.stop_all()
                except Exception:
                    pass
            if app_ref._lsp_manager is not None:
                try:
                    await app_ref._lsp_manager.stop_all()
                except Exception:
                    pass
            if app_ref._runtime is not None and app_ref._runtime.session:
                try:
                    app_ref._session_manager.save(app_ref._runtime.session)
                except Exception:
                    pass

        def _sync_cleanup() -> None:
            try:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(_async_cleanup())
                loop.close()
            except Exception:
                pass

        atexit.register(_sync_cleanup)

    def _init_session(self) -> None:
        """Initialize the runtime with a fresh session."""
        logger.debug(
            "Initialising session: model=%s, provider=%s",
            self._config.model,
            self._config.provider_base_url or "(default)",
        )
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

        context = ProjectContext.discover(self._cwd)
        session = Session.create(self._cwd)

        # Build permission policy
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

        # Init checkpoint manager (only in git repos)
        from llm_code.runtime.checkpoint import CheckpointManager

        checkpoint_mgr = None
        if (self._cwd / ".git").is_dir():
            checkpoint_mgr = CheckpointManager(self._cwd)
        self._checkpoint_mgr = checkpoint_mgr

        # Create token budget if specified
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

        # Register AgentTool if available (needs runtime, built after runtime init)
        try:
            from llm_code.tools.agent import AgentTool

            if self._registry.get("agent") is None:
                agent_tool = AgentTool(
                    runtime_factory=None,
                    max_depth=3,
                    current_depth=0,
                )
                self._registry.register(agent_tool)
        except (ImportError, ValueError):
            pass  # AgentTool not yet available or already registered

        from llm_code.runtime.skills import SkillLoader
        skill_dirs = [
            Path.home() / ".llm-code" / "skills",
            self._cwd / ".llm-code" / "skills",
        ]
        self._skills = SkillLoader().load_from_dirs(skill_dirs)

        # Build project index
        from llm_code.runtime.indexer import ProjectIndexer
        indexer = ProjectIndexer(self._cwd)
        self._project_index = indexer.build_index()

        # Init memory
        from llm_code.runtime.memory import MemoryStore
        memory_dir = Path.home() / ".llm-code" / "memory"
        self._memory = MemoryStore(memory_dir, self._cwd)

        # Register memory tools
        from llm_code.tools.memory_tools import MemoryStoreTool, MemoryRecallTool, MemoryListTool
        for tool_cls in (MemoryStoreTool, MemoryRecallTool, MemoryListTool):
            try:
                self._registry.register(tool_cls(self._memory))
            except ValueError:
                pass  # Already registered

        # Register LSP tools (try/except since LSP servers may not start)
        try:
            from llm_code.lsp.tools import LspGotoDefinitionTool, LspFindReferencesTool, LspDiagnosticsTool
            from llm_code.lsp.manager import LspServerManager  # noqa: F401
            for tool_cls in (LspGotoDefinitionTool, LspFindReferencesTool, LspDiagnosticsTool):
                try:
                    self._registry.register(tool_cls(manager=None))
                except (ValueError, TypeError):
                    pass
        except ImportError:
            pass

    async def run_repl(self) -> None:
        """Run the interactive REPL loop."""
        self._console.print(_BANNER)
        self._register_cleanup()
        self._init_session()
        logger.debug("REPL started")

        while True:
            user_input = await self._input.read("> ")
            if user_input is None:
                # Ctrl+C or Ctrl+D
                self._console.print("\n[dim]Goodbye![/dim]")
                break

            user_input = user_input.strip()
            if not user_input:
                continue

            # Check for slash command
            cmd = parse_slash_command(user_input)
            if cmd is not None:
                should_exit = self._handle_command(cmd)
                if should_exit:
                    break
                continue

            # Regular user message — collect images (pending + paste + drag-drop)
            images = list(self._pending_images)
            self._pending_images.clear()
            pasted = self._input.get_pasted_image()
            if pasted:
                images.append(pasted)
            # Strip the [📎 image pasted] marker from input text
            clean_input = user_input.replace("[📎 image pasted] ", "").strip()
            # Detect drag-and-dropped image file paths in input
            clean_input, dropped = _extract_dropped_images(clean_input)
            images.extend(dropped)
            if not clean_input and images:
                clean_input = "What is in this image?"
            if images:
                self._console.print(f"[dim]📎 Sending with {len(images)} image(s)[/dim]")
            await self._run_turn(clean_input, images=images or None)

    async def run_prompt(self, prompt: str) -> None:
        """Run a single prompt in one-shot mode."""
        self._register_cleanup()
        self._init_session()
        await self._run_turn(prompt)

    async def _run_turn(self, user_input: str, images: list | None = None) -> None:
        """Stream events from a turn and render output."""
        if self._runtime is None:
            self._init_session()

        import re
        from llm_code.api.types import (
            StreamMessageStop, StreamTextDelta, StreamToolProgress,
            StreamToolExecStart, StreamToolExecResult,
        )
        from llm_code.cli.streaming import IncrementalMarkdownRenderer

        streamer = IncrementalMarkdownRenderer(self._console)
        _in_tool_call_tag = False
        _tool_tag_buffer = ""

        import time as _time
        _turn_start = _time.monotonic()
        _first_token = False
        _output_tokens = 0
        _thinking_status = self._console.status(
            "[bold orange3]✱[/] [orange3]Thinking…[/]", spinner="dots", spinner_style="orange3",
        )
        _thinking_status.start()

        assert self._runtime is not None
        self._console.print()  # blank line before response
        async for event in self._runtime.run_turn(user_input, images=images):
            if isinstance(event, StreamTextDelta):
                _output_tokens += len(event.text) // 4  # rough estimate
                if not _first_token:
                    _first_token = True
                    _thinking_status.stop()
                    elapsed = _time.monotonic() - _turn_start
                    self._console.print(f"[dim]({elapsed:.1f}s to first token)[/]")
                # Filter out <tool_call>...</tool_call> tags from display
                text = event.text
                for char in text:
                    if _in_tool_call_tag:
                        _tool_tag_buffer += char
                        if _tool_tag_buffer.endswith("</tool_call>"):
                            _in_tool_call_tag = False
                            _tool_tag_buffer = ""
                    elif _tool_tag_buffer:
                        _tool_tag_buffer += char
                        if _tool_tag_buffer == "<tool_call>":
                            _in_tool_call_tag = True
                        elif not "<tool_call>".startswith(_tool_tag_buffer):
                            # False alarm — flush accumulated chars
                            streamer.feed(_tool_tag_buffer)
                            _tool_tag_buffer = ""
                    elif char == "<":
                        _tool_tag_buffer = "<"
                    else:
                        streamer.feed(char)

            elif isinstance(event, StreamToolExecStart):
                # Stop thinking spinner if still running
                if not _first_token:
                    _thinking_status.stop()
                    _first_token = True
                # Flush any pending text before tool execution
                streamer.finish()
                # Claude Code style: ● ToolName(args)
                self._console.print(
                    f"[bold blue]●[/] [bold]{_format_tool_call(event.tool_name, event.args_summary)}[/]"
                )
                # Show spinner during tool execution
                _thinking_status.update(
                    f"[bold orange3]✱[/] [orange3]Running {event.tool_name}…[/]"
                )
                _thinking_status.start()

            elif isinstance(event, StreamToolExecResult):
                _thinking_status.stop()
                if event.is_error:
                    self._console.print(f"  [dim]└[/] [red]{event.output.strip()[:150]}[/]")
                elif event.tool_name == "edit_file":
                    for line in event.output.strip().splitlines()[:8]:
                        if line.startswith("- "):
                            self._console.print(f"  [dim]└[/] [red]{line}[/]")
                        elif line.startswith("+ "):
                            self._console.print(f"  [dim]└[/] [green]{line}[/]")
                        else:
                            self._console.print(f"  [dim]└[/] {line[:150]}")
                elif event.tool_name in ("write_file", "git_commit"):
                    self._console.print(f"  [dim]└[/] [green]{event.output.strip()[:150]}[/]")
                elif event.tool_name == "read_file":
                    # Show first few lines
                    lines = event.output.strip().splitlines()
                    preview = lines[0][:120] if lines else "(empty)"
                    count = len(lines)
                    self._console.print(f"  [dim]└ {preview}[/]")
                    if count > 1:
                        self._console.print(f"  [dim]  ... ({count} lines)[/]")
                elif event.tool_name == "bash":
                    lines = event.output.strip().splitlines()
                    for line in lines[:8]:
                        self._console.print(f"  [dim]└[/] {line[:150]}")
                    if len(lines) > 8:
                        self._console.print(f"  [dim]  ... ({len(lines)} lines)[/]")
                else:
                    lines = event.output.strip().splitlines() if event.output.strip() else ["(no output)"]
                    for line in lines[:3]:
                        self._console.print(f"  [dim]└[/] {line[:150]}")
                    if len(lines) > 3:
                        self._console.print(f"  [dim]  ... ({len(lines)} lines)[/]")
                self._console.print()

            elif isinstance(event, StreamToolProgress):
                self._renderer.render_tool_progress(
                    event.tool_name, event.message, event.percent,
                )

            elif isinstance(event, StreamMessageStop):
                _thinking_status.stop()
                streamer.finish()
                if event.usage and (event.usage.input_tokens > 0 or event.usage.output_tokens > 0):
                    _output_tokens = event.usage.output_tokens

        # Ensure spinner stopped
        _thinking_status.stop()

        # Flush remaining text
        if _tool_tag_buffer and not _in_tool_call_tag:
            streamer.feed(_tool_tag_buffer)
        streamer.finish()

        # Show turn summary (Claude Code style)
        elapsed = _time.monotonic() - _turn_start
        if elapsed > 0.5:
            tokens_str = f"↓ {_output_tokens:,} tokens" if _output_tokens > 0 else ""
            time_str = f"{elapsed:.1f}s" if elapsed < 60 else f"{elapsed / 60:.1f}m"
            parts = [p for p in [time_str, tokens_str] if p]
            self._console.print(f"\n[dim]({' · '.join(parts)})[/]")

    def _handle_command(self, cmd: SlashCommand) -> bool:
        """Handle a slash command.

        Returns True if the app should exit.
        """
        name = cmd.name
        args = cmd.args.strip()

        if name == "exit" or name == "quit":
            self._console.print("[dim]Goodbye![/dim]")
            return True

        elif name == "help":
            self._renderer.render_help()

        elif name == "clear":
            # Reset session with a new one
            self._init_session()
            self._console.print("[dim]Conversation cleared.[/dim]")

        elif name == "model":
            if args:
                import dataclasses
                self._config = dataclasses.replace(self._config, model=args)
                self._init_session()
                self._console.print(f"[dim]Model switched to: {args}[/dim]")
            else:
                self._console.print(
                    f"[dim]Current model: {self._config.model or '(not set)'}[/dim]"
                )

        elif name == "session":
            self._handle_session_command(args)

        elif name == "config":
            self._handle_config_command(args)

        elif name == "plugin":
            self._handle_plugin_command(args)

        elif name == "skill":
            # Show available skills
            if self._skills:
                for s in self._skills.auto_skills:
                    self._console.print(f"  [green]auto[/green] {s.name} — {s.description}")
                for s in self._skills.command_skills:
                    self._console.print(f"  [cyan]/{s.trigger}[/cyan] {s.name} — {s.description}")
            else:
                self._console.print("[dim]No skills loaded.[/dim]")

        elif name == "cd":
            if args:
                new_path = Path(args).expanduser()
                if not new_path.is_absolute():
                    new_path = self._cwd / new_path
                if new_path.is_dir():
                    self._cwd = new_path
                    os.chdir(new_path)
                    self._console.print(f"[dim]Working directory: {new_path}[/dim]")
                else:
                    self._console.print(f"[red]Directory not found: {new_path}[/red]")
            else:
                self._console.print(f"[dim]Current directory: {self._cwd}[/dim]")

        elif name == "image":
            if args:
                try:
                    img = load_image_from_path(args)
                    self._pending_images.append(img)
                    self._console.print(
                        f"[dim]📎 Image attached: {args} — will be sent with your next message[/dim]"
                    )
                except FileNotFoundError:
                    self._console.print(f"[red]Image not found: {args}[/red]")
            else:
                self._console.print("[red]Usage: /image <path>[/red]")

        elif name == "cost":
            if self._runtime is not None:
                usage = self._runtime.session.total_usage
                self._renderer.render_usage(usage)
            else:
                self._console.print("[dim]No session active.[/dim]")

        elif name == "undo":
            self._handle_undo_command(args)

        elif name == "memory":
            self._handle_memory_command(args)

        elif name == "index":
            self._handle_index_command(args)

        elif name == "lsp":
            self._console.print("[dim]LSP: not yet started in this session[/dim]")

        elif name == "budget":
            if args.strip():
                try:
                    target = int(args.strip())
                    self._budget = target
                    self._console.print(f"[dim]Token budget set: {target:,}[/dim]")
                except ValueError:
                    self._console.print("[red]Usage: /budget <number>[/red]")
            else:
                if self._budget is not None:
                    self._console.print(f"[dim]Current token budget: {self._budget:,}[/dim]")
                else:
                    self._console.print("[dim]No budget set.[/dim]")

        else:
            self._console.print(f"[red]Unknown command: /{name}[/red] — type /help for help")

        return False

    def _handle_undo_command(self, args: str) -> None:
        if not self._checkpoint_mgr:
            self._console.print("[red]Not in a git repository — undo not available.[/red]")
            return

        if args.strip() == "list":
            checkpoints = self._checkpoint_mgr.list_checkpoints()
            if not checkpoints:
                self._console.print("[dim]No checkpoints.[/dim]")
            else:
                from rich.table import Table
                table = Table(title="Checkpoints")
                table.add_column("ID", style="cyan")
                table.add_column("Tool")
                table.add_column("Time")
                table.add_column("SHA", style="dim")
                for cp in checkpoints:
                    table.add_row(cp.id, cp.tool_name, cp.timestamp[:19], cp.git_sha[:8])
                self._console.print(table)
            return

        if not self._checkpoint_mgr.can_undo():
            self._console.print("[dim]Nothing to undo.[/dim]")
            return

        cp = self._checkpoint_mgr.undo()
        if cp:
            self._console.print(f"[green]Undone:[/green] {cp.tool_name} ({cp.tool_args_summary[:50]})")
            self._console.print(f"[dim]Restored to {cp.git_sha[:8]}[/dim]")

    def _handle_memory_command(self, args: str) -> None:
        if not self._memory:
            self._console.print("[red]Memory not initialized.[/red]")
            return
        parts = args.strip().split(None, 2)
        subcmd = parts[0] if parts else ""

        if not subcmd or subcmd == "list":
            entries = self._memory.get_all()
            if not entries:
                self._console.print("[dim]No memories stored.[/dim]")
            else:
                for k, v in entries.items():
                    preview = v.value[:60] + "..." if len(v.value) > 60 else v.value
                    self._console.print(f"  [cyan]{k}[/cyan]: {preview}")
        elif subcmd == "get" and len(parts) > 1:
            val = self._memory.recall(parts[1])
            if val:
                self._console.print(val)
            else:
                self._console.print(f"[red]Key not found: {parts[1]}[/red]")
        elif subcmd == "set" and len(parts) > 2:
            self._memory.store(parts[1], parts[2])
            self._console.print(f"[dim]Stored: {parts[1]}[/dim]")
        elif subcmd == "delete" and len(parts) > 1:
            self._memory.delete(parts[1])
            self._console.print(f"[dim]Deleted: {parts[1]}[/dim]")
        else:
            self._console.print("[red]Usage: /memory [list|get|set|delete] ...[/red]")

    def _handle_index_command(self, args: str) -> None:
        if args.strip() == "rebuild":
            from llm_code.runtime.indexer import ProjectIndexer
            self._project_index = ProjectIndexer(self._cwd).build_index()
            self._console.print(
                f"[dim]Index rebuilt: {len(self._project_index.files)} files, "
                f"{len(self._project_index.symbols)} symbols[/dim]"
            )
        elif self._project_index:
            self._console.print(
                f"[dim]Files: {len(self._project_index.files)}, "
                f"Symbols: {len(self._project_index.symbols)}[/dim]"
            )
            for s in self._project_index.symbols[:20]:
                self._console.print(f"  {s.kind} [cyan]{s.name}[/cyan] — {s.file}:{s.line}")
            if len(self._project_index.symbols) > 20:
                self._console.print(f"  [dim]... and {len(self._project_index.symbols) - 20} more[/dim]")
        else:
            self._console.print("[dim]No index available.[/dim]")

    def _handle_session_command(self, args: str) -> None:
        """Handle /session subcommands: list, save, switch."""
        parts = args.split(None, 1)
        subcmd = parts[0].lower() if parts else "list"
        subargs = parts[1] if len(parts) > 1 else ""

        if subcmd == "list":
            sessions = self._session_manager.list_sessions()
            if not sessions:
                self._console.print("[dim]No saved sessions.[/dim]")
            else:
                from rich.table import Table
                table = Table(title="Saved Sessions")
                table.add_column("ID", style="bold cyan")
                table.add_column("Project")
                table.add_column("Created")
                table.add_column("Messages", justify="right")
                for s in sessions:
                    table.add_row(
                        s.id,
                        str(s.project_path),
                        s.created_at[:19],
                        str(s.message_count),
                    )
                self._console.print(table)

        elif subcmd == "save":
            if self._runtime is not None:
                path = self._session_manager.save(self._runtime.session)
                self._console.print(f"[dim]Session saved: {path}[/dim]")
            else:
                self._console.print("[dim]No active session to save.[/dim]")

        elif subcmd == "switch":
            if not subargs:
                self._console.print("[red]Usage: /session switch <id>[/red]")
                return
            try:
                session = self._session_manager.load(subargs)
                if self._runtime is not None:
                    import dataclasses
                    self._runtime = dataclasses.replace(self._runtime, session=session)
                self._console.print(f"[dim]Switched to session: {subargs}[/dim]")
            except FileNotFoundError:
                self._console.print(f"[red]Session not found: {subargs}[/red]")

        else:
            self._console.print(
                "[red]Unknown session subcommand.[/red] Use: list, save, switch <id>"
            )

    def _handle_plugin_command(self, args: str) -> None:
        """Handle /plugin subcommands: list, enable, disable, uninstall."""
        parts = args.split(None, 1)
        subcmd = parts[0] if parts else "list"
        subargs = parts[1] if len(parts) > 1 else ""

        from llm_code.marketplace.installer import PluginInstaller
        installer = PluginInstaller(Path.home() / ".llm-code" / "plugins")

        if subcmd == "list":
            plugins = installer.list_installed()
            if not plugins:
                self._console.print("[dim]No plugins installed.[/dim]")
            for p in plugins:
                status = "[green]on[/green]" if p.enabled else "[red]off[/red]"
                self._console.print(f"  {status} {p.manifest.name} v{p.manifest.version}")
        elif subcmd == "enable" and subargs:
            installer.enable(subargs)
            self._console.print(f"[dim]Enabled: {subargs}[/dim]")
        elif subcmd == "disable" and subargs:
            installer.disable(subargs)
            self._console.print(f"[dim]Disabled: {subargs}[/dim]")
        elif subcmd == "uninstall" and subargs:
            installer.uninstall(subargs)
            self._console.print(f"[dim]Uninstalled: {subargs}[/dim]")

    def _handle_config_command(self, args: str) -> None:
        """Handle /config set <key> <value>."""
        parts = args.split(None, 2)
        if not parts or parts[0].lower() != "set" or len(parts) < 3:
            self._console.print("[red]Usage: /config set <key> <value>[/red]")
            return

        key = parts[1]
        value = parts[2]

        import dataclasses

        cfg_dict = dataclasses.asdict(self._config)
        if key in cfg_dict:
            # Attempt type coercion
            current = cfg_dict[key]
            try:
                if isinstance(current, bool):
                    typed_value: object = value.lower() in ("true", "1", "yes")
                elif isinstance(current, int):
                    typed_value = int(value)
                elif isinstance(current, float):
                    typed_value = float(value)
                else:
                    typed_value = value
                self._config = dataclasses.replace(self._config, **{key: typed_value})
                self._console.print(f"[dim]Config updated: {key} = {typed_value}[/dim]")
            except (ValueError, TypeError) as e:
                self._console.print(f"[red]Invalid value for {key}: {e}[/red]")
        else:
            self._console.print(f"[red]Unknown config key: {key}[/red]")


if __name__ == "__main__":
    main()
