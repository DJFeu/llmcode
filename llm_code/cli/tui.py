"""llm-code CLI — print-based with Rich output and prompt_toolkit input."""
from __future__ import annotations

import asyncio
import dataclasses
import os
import subprocess
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown

from llm_code.api.client import ProviderClient
from llm_code.api.types import (
    StreamMessageStop,
    StreamTextDelta,
    StreamToolExecResult,
    StreamToolExecStart,
    StreamToolProgress,
)
from llm_code.cli.commands import parse_slash_command
from llm_code.runtime.config import RuntimeConfig
from llm_code.runtime.cost_tracker import CostTracker
from llm_code.runtime.model_aliases import resolve_model
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

console = Console()


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


def _interactive_pick(title: str, items: list[tuple[str, str, bool]], prompt_text: str = "Pick #") -> str | None:
    """Numbered interactive picker. Returns selected item name or None."""
    console.print(f"\n[bold]{title} ({len(items)} items)[/]\n")
    for i, (name, desc, installed) in enumerate(items, 1):
        icon = "[green]●[/]" if installed else "[dim]○[/]"
        num = f"[cyan]{i:>3d}[/]"
        console.print(f"  {num} {icon} [bold]{name}[/]  [dim]· {desc}[/]")

    console.print("\n[dim]Enter number to select, or press Enter to cancel.[/]")
    try:
        choice = input(f"{prompt_text}: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not choice:
        return None
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(items):
            return items[idx][0]
    except ValueError:
        # User typed a name directly
        for name, _, _ in items:
            if name == choice:
                return choice
    console.print("[dim]Cancelled.[/]")
    return None


def _interactive_action(name: str, actions: list[tuple[str, str]]) -> str | None:
    """Pick an action for a selected item."""
    console.print(f"\n[bold]Action for {name}:[/]")
    for i, (action_id, label) in enumerate(actions, 1):
        console.print(f"  [cyan]{i}[/] {label}")
    console.print()
    try:
        choice = input("Pick #: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not choice:
        return None
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(actions):
            return actions[idx][0]
    except ValueError:
        pass
    return None


class LLMCodeCLI:
    """Print-based CLI that renders in the normal terminal scroll buffer."""

    def __init__(
        self,
        config: RuntimeConfig,
        cwd: Path | None = None,
        budget: int | None = None,
    ) -> None:
        self._config = config
        self._cwd = cwd or Path.cwd()
        self._budget = budget
        self._runtime: ConversationRuntime | None = None
        self._tool_reg = ToolRegistry()
        self._session_manager = SessionManager(Path.home() / ".llm-code" / "sessions")
        self._text_buffer = ""
        self._output_tokens = 0
        self._pending_images: list = []
        self._skills = None
        self._memory = None
        self._cost_tracker = CostTracker(model=self._config.model, custom_pricing=self._config.pricing or None)

    # ── Welcome Banner ──────────────────────────────────────────────

    def _render_welcome(self) -> None:
        logo = [
            "  ██╗     ██╗     ███╗   ███╗",
            "  ██║     ██║     ████╗ ████║",
            "  ██║     ██║     ██╔████╔██║",
            "  ██║     ██║     ██║╚██╔╝██║",
            "  ███████╗███████╗██║ ╚═╝ ██║",
            "  ╚══════╝╚══════╝╚═╝     ╚═╝",
            "   ██████╗ ██████╗ ██████╗ ███████╗",
            "  ██╔════╝██╔═══██╗██╔══██╗██╔════╝",
            "  ██║     ██║   ██║██║  ██║█████╗  ",
            "  ██║     ██║   ██║██║  ██║██╔══╝  ",
            "  ╚██████╗╚██████╔╝██████╔╝███████╗",
            "   ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝",
        ]

        model = self._config.model or "(not set)"
        branch = _detect_git_branch(self._cwd)
        workspace = self._cwd.name
        if branch:
            workspace += f" · {branch}"
        perm = self._config.permission_mode or "prompt"
        paste_key = "Cmd+V" if sys.platform == "darwin" else "Ctrl+V"

        info_lines = [
            ("[bold cyan]Local LLM Agent[/]", ""),
            ("[yellow]────────────────────────[/]", ""),
            ("Model", model),
            ("Workspace", workspace),
            ("Directory", str(self._cwd)),
            ("Permissions", perm),
            ("", ""),
            ("Quick start", "/help · /skill · /mcp"),
            ("Multiline", "Shift+Enter"),
            ("Images", f"{paste_key} pastes"),
            ("[yellow]────────────────────────[/]", ""),
            ("[green]Ready[/]", ""),
        ]

        # Print side by side
        console.print()
        max_logo = len(logo)
        max_info = len(info_lines)
        rows = max(max_logo, max_info)

        for i in range(rows):
            # Left: logo
            if i < max_logo:
                left = f"[bold cyan]{logo[i]}[/]"
            else:
                left = " " * 38

            # Right: info
            if i < max_info:
                label, value = info_lines[i]
                if not value and label:
                    right = f"  {label}"
                elif label and value:
                    right = f"  [yellow]{label:<14}[/] [bold white]{value}[/]"
                else:
                    right = ""
            else:
                right = ""

            console.print(f"{left}  {right}")
        console.print()

    # ── Session Initialization ──────────────────────────────────────

    def _init_session(self) -> None:
        """Initialize the conversation runtime."""
        api_key = os.environ.get(self._config.provider_api_key_env, "")
        base_url = self._config.provider_base_url or ""

        resolved_model = resolve_model(
            self._config.model, custom_aliases=self._config.model_aliases
        )
        self._cost_tracker = CostTracker(model=resolved_model, custom_pricing=self._config.pricing or None)

        provider = ProviderClient.from_model(
            model=resolved_model,
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
        self._checkpoint_mgr = checkpoint_mgr

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

    def _flush_text(self) -> None:
        """Flush accumulated text buffer as Markdown."""
        text = self._text_buffer.strip()
        if text:
            console.print(Markdown(text, code_theme="monokai"))
        self._text_buffer = ""

    def _show_tool_start(self, tool_name: str, args_summary: str) -> None:
        import json
        console.print()

        try:
            args = json.loads(args_summary.replace("'", '"'))
        except Exception:
            args = {}
        if not isinstance(args, dict):
            args = {}

        if tool_name == "bash":
            cmd = args.get("command", args_summary)[:120]
            detail = f"[bold white]$[/] [white on color(236)] {cmd} [/]"
        elif tool_name == "read_file":
            path = args.get("path", args_summary)
            detail = f"📄 Reading {path}…"
        elif tool_name == "write_file":
            path = args.get("path", args_summary)
            detail = f"[green]✏️  Writing {path}[/]"
        elif tool_name == "edit_file":
            path = args.get("path", args_summary)
            detail = f"[yellow]📝 Editing {path}[/]"
        elif tool_name in ("glob_search", "grep_search"):
            pattern = args.get("pattern", args.get("glob", args_summary))
            detail = f"🔎 {pattern}"
        elif tool_name == "agent":
            task = args.get("task", args_summary)[:80]
            detail = f"🤖 {task}"
        else:
            parts = tool_name.split("_")
            pretty = "".join(p.capitalize() for p in parts)
            vals = list(args.values())
            summary = str(vals[0])[:80] if vals else args_summary[:80]
            detail = f"{pretty}({summary})"

        border_len = len(tool_name) + 4
        console.print(f"  [grey62]╭─[/] [bold cyan]{tool_name}[/] [grey62]─╮[/]")
        console.print(f"  [grey62]│[/] {detail}")
        console.print(f"  [grey62]╰{'─' * border_len}╯[/]")

    def _show_tool_result(self, tool_name: str, output: str, is_error: bool) -> None:
        if is_error:
            msg = output.strip()[:200]
            console.print(f"  [bold red]✗[/] [red]{msg}[/]")
        elif tool_name == "edit_file":
            raw_lines = output.strip().splitlines()[:12]
            wrote_any = False
            for line in raw_lines:
                if line.startswith("- "):
                    console.print(f"  [color(203)]{line}[/]")
                    wrote_any = True
                elif line.startswith("+ "):
                    console.print(f"  [color(70)]{line}[/]")
                    wrote_any = True
                else:
                    console.print(f"  [grey50]{line[:150]}[/]")
            if not wrote_any:
                console.print(f"  [bold green]✓[/] [grey50]{output.strip()[:150]}[/]")
        elif tool_name in ("write_file", "git_commit"):
            console.print(f"  [bold green]✓[/] [grey50]{output.strip()[:150]}[/]")
        elif tool_name == "read_file":
            lines = output.strip().splitlines()
            preview = lines[0][:120] if lines else "(empty)"
            count = len(lines)
            summary = f"{preview}  … ({count} lines)" if count > 1 else preview
            console.print(f"  [bold green]✓[/] [grey50]{summary}[/]")
            if count > _TRUNCATION_THRESHOLD:
                console.print(
                    "  [grey50]… output truncated for display; full result preserved in session.[/]"
                )
        elif tool_name == "bash":
            raw_lines = output.strip().splitlines()
            for line in raw_lines[:10]:
                console.print(f"  [grey50]{line[:150]}[/]")
            if len(raw_lines) > 10:
                console.print(
                    "  [grey50]… output truncated for display; full result preserved in session.[/]"
                )
            console.print("  [bold green]✓[/] [grey50]done[/]")
        else:
            raw_lines = output.strip().splitlines() if output.strip() else ["(no output)"]
            for line in raw_lines[:5]:
                console.print(f"  [grey50]{line[:150]}[/]")
            if len(raw_lines) > 5:
                console.print(
                    "  [grey50]… output truncated for display; full result preserved in session.[/]"
                )
            console.print("  [bold green]✓[/] [grey50]done[/]")

        console.print()

    # ── NPM Fetchers ───────────────────────────────────────────────

    async def _fetch_marketplace_skills(self) -> list[tuple[str, str]]:
        """Fetch skills: npm (Claude official) first, then ClawHub (44k+ community)."""
        results: list[tuple[str, str]] = []
        # 1. npm — Claude official skills
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    "https://registry.npmjs.org/-/v1/search",
                    params={"text": "claude-code skill", "size": 20},
                )
                resp.raise_for_status()
                data = resp.json()
            for obj in data.get("objects", []):
                pkg = obj.get("package", {})
                name = pkg.get("name", "")
                desc = pkg.get("description", "")[:70]
                if "skill" in name.lower() or ("claude" in name.lower() and "skill" in desc.lower()):
                    results.append((name, f"[npm] {desc}"))
        except Exception:
            pass
        # 2. ClawHub — 44k+ community skills
        try:
            from llm_code.marketplace.builtin_registry import search_clawhub_skills
            clawhub = await search_clawhub_skills("", limit=80)
            for slug, desc in clawhub:
                results.append((f"clawhub:{slug}", f"[ClawHub] {desc}"))
        except Exception:
            pass
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

    # ── Slash Commands ──────────────────────────────────────────────

    def _handle_slash_command(self, text: str) -> None:
        cmd = parse_slash_command(text)
        if cmd is None:
            return

        name = cmd.name
        args = cmd.args.strip()

        if name in ("exit", "quit"):
            console.print("[dim]Goodbye![/]")
            raise SystemExit(0)

        elif name == "help":
            console.print("[bold]Available commands:[/]")
            for cmd_name, desc in [
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
                console.print(f"  [dim]{cmd_name:<30s} {desc}[/]")
            console.print()

        elif name == "clear":
            self._init_session()
            self._render_welcome()
            console.print("[dim]Conversation cleared.[/]")

        elif name == "model":
            if args:
                self._config = dataclasses.replace(self._config, model=args)
                self._init_session()
                console.print(f"[dim]Model switched to: {args}[/]")
            else:
                console.print(f"[dim]Current model: {self._config.model or '(not set)'}[/]")

        elif name == "cost":
            console.print(f"[dim]{self._cost_tracker.format_cost()}[/]")

        elif name == "cd":
            if args:
                new_path = Path(args).expanduser()
                if not new_path.is_absolute():
                    new_path = self._cwd / new_path
                if new_path.is_dir():
                    self._cwd = new_path
                    os.chdir(new_path)
                    console.print(f"[dim]Working directory: {new_path}[/]")
                else:
                    console.print(f"[red]Directory not found: {new_path}[/]")
            else:
                console.print(f"[dim]Current directory: {self._cwd}[/]")

        elif name == "budget":
            if args:
                try:
                    target = int(args)
                    self._budget = target
                    console.print(f"[dim]Token budget set: {target:,}[/]")
                except ValueError:
                    console.print("[red]Usage: /budget <number>[/]")
            else:
                if self._budget is not None:
                    console.print(f"[dim]Current token budget: {self._budget:,}[/]")
                else:
                    console.print("[dim]No budget set.[/]")

        elif name == "skill":
            self._handle_skill_command(args)

        elif name == "mcp":
            self._handle_mcp_command(args)

        elif name == "plugin":
            self._handle_plugin_command(args)

        elif name == "memory":
            self._handle_memory_command(args)

        elif name == "undo":
            if hasattr(self, '_checkpoint_mgr') and self._checkpoint_mgr:
                if args.strip() == "list":
                    for cp in self._checkpoint_mgr.list_checkpoints():
                        console.print(f"  [dim]{cp.id}  {cp.tool_name}  {cp.timestamp[:19]}[/]")
                elif self._checkpoint_mgr.can_undo():
                    cp = self._checkpoint_mgr.undo()
                    if cp:
                        console.print(
                            f"[green]Undone: {cp.tool_name} ({cp.tool_args_summary[:50]})[/]"
                        )
                else:
                    console.print("[dim]Nothing to undo.[/]")
            else:
                console.print("[red]Not in a git repository — undo not available.[/]")

        elif name == "index":
            if args.strip() == "rebuild":
                from llm_code.runtime.indexer import ProjectIndexer
                idx = ProjectIndexer(self._cwd).build_index()
                console.print(
                    f"[dim]Index rebuilt: {len(idx.files)} files, {len(idx.symbols)} symbols[/]"
                )
            elif hasattr(self, '_project_index') and self._project_index:
                console.print(
                    f"[dim]Files: {len(self._project_index.files)}, "
                    f"Symbols: {len(self._project_index.symbols)}[/]"
                )
                for s in self._project_index.symbols[:20]:
                    console.print(f"  [dim]{s.kind} {s.name} — {s.file}:{s.line}[/]")
            else:
                console.print("[dim]No index available.[/]")

        elif name == "session":
            parts = args.split(None, 1)
            subcmd = parts[0] if parts else "list"
            if subcmd == "list":
                sessions = self._session_manager.list_sessions()
                if not sessions:
                    console.print("[dim]No saved sessions.[/]")
                for s in sessions:
                    console.print(f"  [dim]{s.id}  {s.project_path}  ({s.message_count} msgs)[/]")
            elif subcmd == "save" and self._runtime:
                path = self._session_manager.save(self._runtime.session)
                console.print(f"[dim]Session saved: {path}[/]")

        elif name == "image":
            if args:
                from llm_code.cli.image import load_image_from_path
                try:
                    img = load_image_from_path(args)
                    self._pending_images.append(img)
                    console.print(f"[dim]📎 Image attached: {args}[/]")
                except FileNotFoundError:
                    console.print(f"[red]Image not found: {args}[/]")
            else:
                console.print("[red]Usage: /image <path>[/]")

        elif name == "lsp":
            console.print("[dim]LSP: not started in this session.[/]")

        else:
            console.print(f"[red]Unknown command: /{name} -- type /help for help[/]")

    # ── Marketplace Handlers ─────────────────────────────────────────

    def _handle_skill_command(self, args: str) -> None:
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
                import tempfile
                with tempfile.TemporaryDirectory() as tmp:
                    console.print(f"[blue]⠋ Cloning {repo}...[/]")
                    result = _sp.run(
                        ["git", "clone", "--depth", "1", f"https://github.com/{repo}.git", tmp],
                        capture_output=True, text=True, timeout=30,
                    )
                    if result.returncode == 0:
                        skills_src = Path(tmp) / "skills"
                        if skills_src.is_dir():
                            import shutil
                            shutil.copytree(skills_src, dest)
                            console.print(f"[green]✓ Installed skills from {repo}[/]")
                        else:
                            import shutil
                            shutil.copytree(tmp, dest)
                            console.print(f"[green]✓ Installed {name}[/]")
                        console.print("  [dim]Restart llm-code to activate.[/]")
                    else:
                        console.print(f"[red]✗ Failed: {result.stderr[:200]}[/]")
            else:
                # npm package
                skill_dir = Path.home() / ".llm-code" / "skills" / source.split("/")[-1].replace("@", "")
                skill_dir.mkdir(parents=True, exist_ok=True)
                result = _sp.run(
                    ["npm", "pack", source, "--pack-destination", str(skill_dir)],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode == 0:
                    import glob as _glob
                    tarballs = _glob.glob(str(skill_dir / "*.tgz"))
                    if tarballs:
                        _sp.run(
                            ["tar", "xzf", tarballs[0], "-C", str(skill_dir), "--strip-components=1"],
                            capture_output=True, timeout=10,
                        )
                        Path(tarballs[0]).unlink(missing_ok=True)
                    console.print(f"[green]✓ Installed to {skill_dir}[/]")
                else:
                    console.print(f"[red]✗ Failed: {result.stderr[:200]}[/]")
            return

        if subcmd == "enable" and subargs:
            marker = Path.home() / ".llm-code" / "skills" / subargs / ".disabled"
            marker.unlink(missing_ok=True)
            console.print(f"[green]✓ Enabled {subargs}[/]")
        elif subcmd == "disable" and subargs:
            marker = Path.home() / ".llm-code" / "skills" / subargs / ".disabled"
            marker.touch()
            console.print(f"[dim]Disabled {subargs}[/]")
        elif subcmd == "remove" and subargs:
            import shutil
            d = Path.home() / ".llm-code" / "skills" / subargs
            if d.is_dir():
                shutil.rmtree(d)
                console.print(f"[green]✓ Removed {subargs}[/]")
            else:
                console.print(f"[red]Not found: {subargs}[/]")
        else:
            # Interactive picker: installed + npm marketplace
            all_skills = []
            if self._skills:
                all_skills = list(self._skills.auto_skills) + list(self._skills.command_skills)

            items: list[tuple[str, str, bool]] = []
            for s in all_skills:
                tokens = len(s.content) // 4
                mode = "auto" if s.auto else f"/{s.trigger}"
                items.append((s.name, f"{mode} · ~{tokens} tokens", True))

            # Fetch npm marketplace
            import asyncio as _aio
            try:
                try:
                    _aio.get_running_loop()
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        market = pool.submit(_aio.run, self._fetch_marketplace_skills()).result(timeout=5)
                except RuntimeError:
                    market = _aio.run(self._fetch_marketplace_skills())
            except Exception:
                market = []
                console.print("[dim]  (marketplace fetch timed out — showing local skills only)[/]")

            installed_names = {s.name for s in all_skills}
            for name, desc in market:
                if name not in installed_names:
                    items.append((name, desc, False))

            selected = _interactive_pick("Skills", items)
            if not selected:
                return

            is_installed = selected in installed_names
            actions = []
            if is_installed:
                actions.append(("enable", f"Enable {selected}"))
                actions.append(("disable", f"Disable {selected}"))
                actions.append(("remove", f"Remove {selected}"))
            else:
                actions.append(("install", f"Install {selected}"))

            action = _interactive_action(selected, actions)
            if action == "install":
                self._handle_skill_command(f"install {selected}")
            elif action in ("enable", "disable", "remove"):
                self._handle_skill_command(f"{action} {selected}")

    def _handle_mcp_command(self, args: str) -> None:
        parts = args.strip().split(None, 1)
        subcmd = parts[0] if parts else ""
        subargs = parts[1] if len(parts) > 1 else ""

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
            config.setdefault("mcpServers", {})[server_name] = {
                "command": "npx", "args": ["-y", package]
            }
            config_path.write_text(json.dumps(config, indent=2))
            console.print(f"[green]✓ Added {server_name} to config. Restart to activate.[/]")
        elif subcmd == "remove" and subargs:
            import json
            config_path = Path.home() / ".llm-code" / "config.json"
            if config_path.exists():
                config = json.loads(config_path.read_text())
                if subargs in config.get("mcpServers", {}):
                    del config["mcpServers"][subargs]
                    config_path.write_text(json.dumps(config, indent=2))
                    console.print(f"[green]✓ Removed {subargs}[/]")
                else:
                    console.print(f"[red]Not found: {subargs}[/]")
        else:
            # List configured + marketplace
            servers = self._config.mcp_servers
            console.print(f"\n[bold]MCP Servers ({len(servers)} configured)[/]")
            for name, cfg in servers.items():
                cmd = cfg.get("command", "")
                srv_args = " ".join(cfg.get("args", []))
                console.print(f"  [green]●[/] {name}  [dim]· {cmd} {srv_args}[/]")
            if not servers:
                console.print("  [dim]No MCP servers configured.[/]")

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

            available = [(n, d) for n, d in market if n not in servers]
            if available:
                console.print(f"\n[bold]Marketplace ({len(available)} available)[/]")
                for name, desc in available[:20]:
                    console.print(f"  [dim]○[/] {name}  [dim]· {desc}[/]")

            console.print(
                "\n[dim]Install: /mcp install <npm-package>  |  Remove: /mcp remove <name>[/]"
            )

    def _handle_plugin_command(self, args: str) -> None:
        from llm_code.marketplace.installer import PluginInstaller
        installer = PluginInstaller(Path.home() / ".llm-code" / "plugins")
        parts = args.strip().split(None, 1)
        subcmd = parts[0] if parts else ""
        subargs = parts[1] if len(parts) > 1 else ""

        if subcmd == "install" and subargs:
            source = subargs.strip()
            import subprocess as _sp
            if "/" in source and not source.startswith("@"):
                # GitHub repo
                repo = source.replace("https://github.com/", "").rstrip("/")
                name = repo.split("/")[-1]
                dest = Path.home() / ".llm-code" / "plugins" / name
                if dest.exists():
                    import shutil
                    shutil.rmtree(dest)
                console.print(f"[blue]⠋ Cloning {repo}...[/]")
                result = _sp.run(
                    ["git", "clone", "--depth", "1", f"https://github.com/{repo}.git", str(dest)],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode == 0:
                    installer.enable(name)
                    console.print(f"[green]✓ Installed {name} from GitHub[/]")
                    console.print(f"  [dim]{dest}[/]")
                    console.print("  [dim]Restart llm-code to activate skills & hooks.[/]")
                else:
                    console.print(f"[red]✗ Failed: {result.stderr[:200]}[/]")
            else:
                console.print("[red]Usage: /plugin install owner/repo[/]")
            return

        if subcmd == "enable" and subargs:
            installer.enable(subargs)
            console.print(f"[green]✓ Enabled {subargs}[/]")
        elif subcmd == "disable" and subargs:
            installer.disable(subargs)
            console.print(f"[dim]Disabled {subargs}[/]")
        elif subcmd in ("remove", "uninstall") and subargs:
            installer.uninstall(subargs)
            console.print(f"[green]✓ Removed {subargs}[/]")
        else:
            # Interactive picker: installed + registry
            installed = installer.list_installed()
            installed_names = {p.manifest.name for p in installed}

            items: list[tuple[str, str, bool]] = []
            for p in installed:
                items.append((p.manifest.name, f"v{p.manifest.version}", True))

            from llm_code.marketplace.builtin_registry import get_all_known_plugins
            for p in get_all_known_plugins():
                if p["name"] not in installed_names:
                    skill_info = f"{p['skills']} skills · " if p["skills"] > 0 else ""
                    items.append((p["name"], f"{skill_info}{p['desc']}", False))

            selected = _interactive_pick("Plugins", items)
            if not selected:
                return

            is_installed = selected in installed_names
            actions = []
            if is_installed:
                actions.append(("enable", f"Enable {selected}"))
                actions.append(("disable", f"Disable {selected}"))
                actions.append(("remove", f"Remove {selected}"))
            else:
                registry = {p["name"]: p for p in get_all_known_plugins()}
                repo = registry.get(selected, {}).get("repo", "")
                if repo:
                    actions.append(("install", f"Install {selected} (from {repo})"))
                else:
                    actions.append(("install_manual", f"Install {selected} (need repo URL)"))

            action = _interactive_action(selected, actions)
            if action == "install":
                registry = {p["name"]: p for p in get_all_known_plugins()}
                repo = registry.get(selected, {}).get("repo", "")
                if repo:
                    self._handle_plugin_command(f"install {repo}")
            elif action == "install_manual":
                console.print(f"[dim]Run: /plugin install owner/{selected}[/]")
            elif action in ("enable", "disable", "remove"):
                self._handle_plugin_command(f"{action} {selected}")

    def _handle_memory_command(self, args: str) -> None:
        if not self._memory:
            console.print("[red]Memory not initialized.[/]")
            return
        parts = args.strip().split(None, 2)
        subcmd = parts[0] if parts else ""

        if subcmd == "set" and len(parts) > 2:
            self._memory.store(parts[1], parts[2])
            console.print(f"[dim]Stored: {parts[1]}[/]")
        elif subcmd == "get" and len(parts) > 1:
            val = self._memory.recall(parts[1])
            if val:
                console.print(f"[dim]{val}[/]")
            else:
                console.print(f"[red]Key not found: {parts[1]}[/]")
        elif subcmd == "delete" and len(parts) > 1:
            self._memory.delete(parts[1])
            console.print(f"[dim]Deleted: {parts[1]}[/]")
        else:
            entries = self._memory.get_all()
            console.print(f"[bold]Memory ({len(entries)} entries)[/]")
            for k, v in entries.items():
                console.print(f"  [dim]{k}: {v.value[:60]}[/]")
            if not entries:
                console.print("  [dim]No memories stored.[/]")

    # ── Streaming Turn ──────────────────────────────────────────────

    async def _run_turn(self, user_input: str, images: list | None = None) -> None:
        """Stream response with tool calls — print to normal scroll buffer."""
        if self._runtime is None:
            self._init_session()

        assert self._runtime is not None

        console.print(f"\n[bold white]❯[/] [bold white]{user_input}[/]")
        console.print()

        start = time.monotonic()
        self._text_buffer = ""
        self._output_tokens = 0
        first_token = False

        # Tag filter state: hide <tool_call> and <think> blocks
        in_tool_call_tag = False
        in_think_tag = False
        tool_tag_buffer = ""

        status = console.status("[blue]⠋ Thinking…[/]", spinner="dots")
        status.start()

        try:
            async for event in self._runtime.run_turn(user_input, images=images):
                if isinstance(event, StreamTextDelta):
                    self._output_tokens += len(event.text) // 4

                    if not first_token:
                        first_token = True
                        status.stop()

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
                    status.stop()
                    self._flush_text()
                    self._show_tool_start(event.tool_name, event.args_summary)
                    status.update(f"[blue]Running {event.tool_name}…[/]")
                    status.start()

                elif isinstance(event, StreamToolExecResult):
                    status.stop()
                    self._show_tool_result(event.tool_name, event.output, event.is_error)

                elif isinstance(event, StreamToolProgress):
                    status.update(f"[blue]{event.tool_name}: {event.message}[/]")

                elif isinstance(event, StreamMessageStop):
                    status.stop()
                    self._flush_text()
                    if event.usage and (
                        event.usage.input_tokens > 0 or event.usage.output_tokens > 0
                    ):
                        self._output_tokens = event.usage.output_tokens
                        self._cost_tracker.add_usage(
                            event.usage.input_tokens, event.usage.output_tokens
                        )

        except KeyboardInterrupt:
            status.stop()
            self._flush_text()
            console.print("[dim](cancelled)[/]")
            return
        except Exception as exc:
            status.stop()
            console.print(f"[bold red]Error: {exc}[/]")
            return

        status.stop()

        # Flush remaining text
        if tool_tag_buffer and not in_tool_call_tag and not in_think_tag:
            self._text_buffer += tool_tag_buffer
        self._flush_text()

        # Turn summary
        elapsed = time.monotonic() - start
        time_str = f"{elapsed:.1f}s" if elapsed < 60 else f"{elapsed / 60:.1f}m"
        tokens_str = f"  ↓{self._output_tokens:,} tok" if self._output_tokens > 0 else ""
        console.print(f"[bold green]✓[/] [green]Done ({time_str})[/][dim]{tokens_str}[/]")
        console.print()

    # ── Main REPL Loop ──────────────────────────────────────────────

    async def run(self) -> None:
        """Main REPL loop using prompt_toolkit for input."""
        from prompt_toolkit import PromptSession
        from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
        from prompt_toolkit.completion import WordCompleter
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.keys import Keys

        self._render_welcome()
        self._init_session()

        bindings = KeyBindings()

        @bindings.add("c-v")
        def _paste_ctrl_v(event):
            from llm_code.cli.image import capture_clipboard_image
            img = capture_clipboard_image()
            if img:
                self._pending_images.append(img)
                event.app.current_buffer.insert_text("[image] ")
            else:
                event.app.current_buffer.paste_clipboard_data(
                    event.app.clipboard.get_data()
                )

        @bindings.add(Keys.BracketedPaste)
        def _bracketed_paste(event):
            from llm_code.cli.image import capture_clipboard_image
            img = capture_clipboard_image()
            if img:
                event.prevent_default()
                event.stop()
                self._pending_images.append(img)
                event.app.current_buffer.insert_text("[image] ")
            else:
                pasted = event.data if hasattr(event, "data") else ""
                if pasted:
                    event.app.current_buffer.insert_text(pasted)

        history_path = Path.home() / ".llm-code" / "history"
        history_path.parent.mkdir(parents=True, exist_ok=True)

        SLASH_COMMANDS = [
            "/help", "/clear", "/model", "/skill", "/skill search", "/skill install",
            "/skill enable", "/skill disable", "/skill remove",
            "/mcp", "/mcp install", "/mcp remove", "/mcp search",
            "/plugin", "/plugin install", "/plugin enable", "/plugin disable", "/plugin remove",
            "/memory", "/memory get", "/memory set", "/memory delete",
            "/session list", "/session save", "/session switch",
            "/undo", "/undo list", "/index", "/index rebuild",
            "/image", "/cost", "/budget", "/cd", "/lsp", "/exit",
        ]

        session = PromptSession(
            history=FileHistory(str(history_path)),
            auto_suggest=AutoSuggestFromHistory(),
            completer=WordCompleter(SLASH_COMMANDS, sentence=True),
            key_bindings=bindings,
        )

        while True:
            try:
                user_input = await session.prompt_async("❯ ")
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]Goodbye![/]")
                break

            user_input = user_input.strip()
            if not user_input:
                continue

            # Collect images
            images = list(self._pending_images)
            self._pending_images.clear()

            # Detect dropped image paths
            from llm_code.cli.image import extract_dropped_images
            clean_input, dropped = extract_dropped_images(user_input)
            images.extend(dropped)

            # Strip image paste marker
            clean_input = clean_input.replace("[image]", "").replace("[image pasted]", "").strip()
            if not clean_input and images:
                clean_input = "What is in this image?"

            if clean_input.startswith("/"):
                self._handle_slash_command(clean_input)
                continue

            if images:
                console.print(f"[dim]📎 Sending with {len(images)} image(s)[/]")

            await self._run_turn(clean_input, images=images or None)


# ── Backwards compatibility alias ──────────────────────────────────
# tui_main.py and external callers may still reference LLMCodeApp
class LLMCodeApp:
    """Backwards-compatible shim: wraps LLMCodeCLI with a .run() that calls asyncio.run()."""

    def __init__(
        self,
        config: RuntimeConfig,
        cwd: Path | None = None,
        budget: int | None = None,
    ) -> None:
        self._cli = LLMCodeCLI(config=config, cwd=cwd, budget=budget)

    def run(self) -> None:
        asyncio.run(self._cli.run())
