"""Main CLI application entry point."""
from __future__ import annotations

import asyncio
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
from llm_code.runtime.config import RuntimeConfig, load_config
from llm_code.runtime.context import ProjectContext
from llm_code.runtime.conversation import ConversationRuntime
from llm_code.runtime.hooks import HookRunner
from llm_code.runtime.permissions import PermissionMode, PermissionPolicy
from llm_code.runtime.prompt import SystemPromptBuilder
from llm_code.runtime.session import Session, SessionManager
from llm_code.tools.bash import BashTool
from llm_code.tools.edit_file import EditFileTool
from llm_code.tools.glob_search import GlobSearchTool
from llm_code.tools.grep_search import GrepSearchTool
from llm_code.tools.read_file import ReadFileTool
from llm_code.tools.registry import ToolRegistry
from llm_code.tools.write_file import WriteFileTool

_BANNER = """[bold cyan]llm-code[/bold cyan] — AI coding assistant
Type [bold]/help[/bold] for commands, [bold]/exit[/bold] to quit.
"""

_PERMISSION_CHOICES = ["prompt", "auto_accept", "read_only", "workspace_write", "full_access"]


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
def main(
    prompt: str | None,
    model: str | None,
    api: str | None,
    api_key: str | None,
    permission: str | None,
) -> None:
    """llm-code: AI coding assistant CLI."""
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
    user_dir = Path.home() / ".config" / "llm-code"
    config = load_config(
        user_dir=user_dir,
        project_dir=cwd,
        local_path=cwd / ".llm-code" / "config.json",
        cli_overrides=cli_overrides,
    )

    app = CliApp(config=config, cwd=cwd)

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

    def __init__(self, config: RuntimeConfig, cwd: Path | None = None) -> None:
        self._config = config
        self._cwd = cwd or Path.cwd()
        self._console = Console()
        self._renderer = TerminalRenderer(self._console)
        self._input = InputHandler(
            history_path=Path.home() / ".config" / "llm-code" / "history"
        )

        # Build tool registry with all 6 tools
        self._registry = ToolRegistry()
        self._registry.register(ReadFileTool())
        self._registry.register(WriteFileTool())
        self._registry.register(EditFileTool())
        self._registry.register(BashTool())
        self._registry.register(GlobSearchTool())
        self._registry.register(GrepSearchTool())

        # Session manager
        session_dir = Path.home() / ".config" / "llm-code" / "sessions"
        self._session_manager = SessionManager(session_dir)

        # Runtime will be initialized on first use
        self._runtime: ConversationRuntime | None = None

    def _init_session(self) -> None:
        """Initialize the runtime with a fresh session."""
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

        self._runtime = ConversationRuntime(
            provider=provider,
            tool_registry=self._registry,
            permission_policy=permissions,
            hook_runner=hooks,
            prompt_builder=prompt_builder,
            config=self._config,
            session=session,
            context=context,
        )

    async def run_repl(self) -> None:
        """Run the interactive REPL loop."""
        self._console.print(_BANNER)
        self._init_session()

        while True:
            user_input = self._input.read("> ")
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

            # Regular user message
            await self._run_turn(user_input)

    async def run_prompt(self, prompt: str) -> None:
        """Run a single prompt in one-shot mode."""
        self._init_session()
        await self._run_turn(prompt)

    async def _run_turn(self, user_input: str) -> None:
        """Stream events from a turn and render output."""
        if self._runtime is None:
            self._init_session()

        import re

        from llm_code.api.types import StreamTextDelta, StreamMessageStop

        text_buffer: list[str] = []

        assert self._runtime is not None
        async for event in self._runtime.run_turn(user_input):
            if isinstance(event, StreamTextDelta):
                text_buffer.append(event.text)
            elif isinstance(event, StreamMessageStop):
                # Render accumulated text
                full_text = "".join(text_buffer)
                # Strip XML tool_call tags if present
                clean_text = re.sub(
                    r"<tool_call>.*?</tool_call>", "", full_text, flags=re.DOTALL
                ).strip()
                if clean_text:
                    self._renderer.render_markdown(clean_text)
                text_buffer.clear()
                # Render usage
                if event.usage:
                    self._renderer.render_usage(event.usage)

        # In case we got text without a stop event
        if text_buffer:
            full_text = "".join(text_buffer)
            clean_text = re.sub(
                r"<tool_call>.*?</tool_call>", "", full_text, flags=re.DOTALL
            ).strip()
            if clean_text:
                self._renderer.render_markdown(clean_text)

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
                    self._console.print(
                        f"[dim]Image loaded: {args} ({img.media_type})[/dim]"
                    )
                    # TODO: attach to next message
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

        else:
            self._console.print(f"[red]Unknown command: /{name}[/red] — type /help for help")

        return False

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
