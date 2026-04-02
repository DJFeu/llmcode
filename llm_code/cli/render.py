"""Rich-based terminal renderer for the CLI layer."""
from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from llm_code.api.types import TokenUsage
from llm_code.tools.base import ToolResult


# File extensions to language mappings for syntax highlighting
_EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".jsx": "jsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".md": "markdown",
    ".html": "html",
    ".css": "css",
    ".sql": "sql",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".r": "r",
}

SLASH_COMMANDS_HELP = [
    ("/help", "Show this help message"),
    ("/clear", "Clear the conversation history"),
    ("/model [name]", "Show or switch the current model"),
    ("/session list", "List saved sessions"),
    ("/session save", "Save the current session"),
    ("/session switch <id>", "Switch to a saved session"),
    ("/config set <key> <value>", "Set a runtime config value"),
    ("/cd <path>", "Change the working directory"),
    ("/image <path>", "Attach an image from file path"),
    ("/cost", "Show token usage and estimated cost"),
    ("/plugin list|enable|disable|uninstall", "Manage plugins"),
    ("/skill", "List available skills"),
    ("/exit", "Exit the application"),
]


class TerminalRenderer:
    """Renders CLI output using Rich."""

    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console()

    def render_markdown(self, text: str) -> None:
        """Render text as Rich Markdown."""
        self._console.print(Markdown(text))

    def render_tool_panel(
        self,
        tool_name: str,
        args: dict,
        result: ToolResult,
    ) -> None:
        """Render a tool call result in a panel with syntax highlighting."""
        status_color = "red" if result.is_error else "green"
        status_icon = "[red]✗[/red]" if result.is_error else "[green]✓[/green]"
        title = f"{status_icon} [bold]{tool_name}[/bold]"

        # Determine content to display
        output = result.output or ""

        # Syntax highlight based on context
        if tool_name == "read_file":
            file_path = args.get("path", "")
            ext = Path(file_path).suffix.lower()
            lang = _EXT_TO_LANG.get(ext, "text")
            if output:
                content = Syntax(output, lang, theme="monokai", line_numbers=True)
            else:
                content = Text(output)
        elif tool_name == "bash":
            content = Syntax(output, "bash", theme="monokai") if output else Text(output)
        else:
            content = Text(output)

        self._console.print(
            Panel(
                content,
                title=title,
                border_style=status_color,
                expand=False,
            )
        )

    def render_permission_prompt(self, tool_name: str, args: dict) -> None:
        """Render a permission prompt for a tool call."""
        import json

        args_str = json.dumps(args, indent=2)
        content = (
            f"[bold yellow]Tool:[/bold yellow] {tool_name}\n"
            f"[bold yellow]Args:[/bold yellow]\n{args_str}"
        )
        self._console.print(
            Panel(
                content,
                title="[bold yellow]Permission Required[/bold yellow]",
                border_style="yellow",
                expand=False,
            )
        )
        self._console.print("[bold]Allow? [y/n/a(lways)/never][/bold] ", end="")

    def render_usage(self, usage: TokenUsage) -> None:
        """Render token usage statistics."""
        total = usage.input_tokens + usage.output_tokens
        self._console.print(
            f"[dim]Tokens — input: {usage.input_tokens:,}  "
            f"output: {usage.output_tokens:,}  "
            f"total: {total:,}[/dim]"
        )

    def render_tool_progress(self, tool_name: str, message: str, percent: float | None = None) -> None:
        """Render an in-progress update for a running tool (overwrites current line)."""
        if percent is not None:
            pct = f"{percent:.0%}"
            self._console.print(f"  [dim]{tool_name}[/dim] {message} [{pct}]", end="\r")
        else:
            self._console.print(f"  [dim]{tool_name}[/dim] {message}", end="\r")

    def render_help(self) -> None:
        """Render a table of available slash commands."""
        table = Table(title="Available Commands", show_header=True, header_style="bold cyan")
        table.add_column("Command", style="bold green", no_wrap=True)
        table.add_column("Description")

        for cmd, desc in SLASH_COMMANDS_HELP:
            table.add_row(cmd, desc)

        self._console.print(table)
