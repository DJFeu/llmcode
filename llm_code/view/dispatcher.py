"""CommandDispatcher — view-agnostic slash-command router for v2.0.0.

M10.6 deliverable. Replaces ``tui/command_dispatcher.py`` (2455 lines,
52 _cmd_* methods, Textual widget coupled) with a view-agnostic
implementation that calls ViewBackend Protocol methods and mutates
AppState directly.

Architecture:
  backend.set_input_handler(dispatcher.run_turn) ← M11 wiring
         │
         ▼
  async run_turn(text)
    ├─ text.startswith("/") ?
    │    ├─ dispatch(name, args) → sync _cmd_* handler
    │    ├─ custom-command file in .llmcode/commands/
    │    └─ skill command trigger
    └─ plain text → await renderer.run_turn(text)

This file is being ported in 4 batches per the M10 plan:

- **Batch A (this commit):** ~15 core commands — run_turn routing,
  dispatch(), helpers, and the commands that are essential for M11
  to ship a usable REPL: help, clear, exit, quit, cost, cancel, cd,
  budget, plan, yolo, mode, thinking, gain, profile, diff.
- **Batch B (next):** ~15 runtime-state mutation commands (model,
  undo, checkpoint, set, config, settings, theme, harness, etc.)
- **Batch C (next next):** ~17 feature-module commands (skill,
  memory, swarm, task, cron, ide, lsp, hida, mcp, voice, session,
  plugin, map, search, knowledge, dump, analyze, orchestrate,
  personas, vcr, diff_check, index, init, compact, export)
- **Batch D (final):** ~5 remaining — copy, image, vim, update,
  lsp (informational), cache.

Commands that are Textual-modal-only (help with tabs, knowledge
browser, memory browser, skill browser) get simplified inline print
versions in v2.0.0 — no modal screens. The M10 invariant is "every
feature works", not "every feature looks the same".
"""
from __future__ import annotations

import dataclasses
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from llm_code.logging import get_logger

if TYPE_CHECKING:
    from llm_code.runtime.app_state import AppState
    from llm_code.view.base import ViewBackend
    from llm_code.view.stream_renderer import ViewStreamRenderer

logger = get_logger(__name__)


class CommandDispatcher:
    """Slash-command router for v2.0.0 REPL.

    Constructed with a ``ViewBackend``, an ``AppState``, and a
    ``ViewStreamRenderer``. The backend provides all output surfaces
    (print_info, start_streaming_message, show_select, ...) plus
    ``request_exit``; the state holds runtime/config/subsystems; the
    renderer handles plain-text turns.

    Handlers are named ``_cmd_<name>(args: str) -> None``. ``dispatch``
    returns ``True`` if a handler was found, ``False`` otherwise so the
    caller can fall through to custom commands / skill commands /
    unknown-command diagnostics.
    """

    def __init__(
        self,
        view: "ViewBackend",
        state: "AppState",
        renderer: "ViewStreamRenderer",
    ) -> None:
        self._view = view
        self._state = state
        self._renderer = renderer

    # ── top-level entry point ────────────────────────────────────

    async def run_turn(
        self,
        text: str,
        images: Optional[list] = None,
    ) -> None:
        """Top-level input handler installed via
        ``backend.set_input_handler(dispatcher.run_turn)``.

        Handles three cases:

        1. **Slash command** (text starts with ``/``) — parse, dispatch,
           fall through to custom-commands / skill-commands, then
           "unknown command" with a close-match hint.
        2. **Plain text** — forward to
           ``ViewStreamRenderer.run_turn(text, images=...)`` which
           streams the LLM turn into the view.

        Never raises; all exceptions are surfaced via ``view.print_error``
        so the backend's event loop keeps running.
        """
        stripped = text.strip()
        if not stripped:
            return

        if stripped.startswith("/"):
            await self._handle_slash_command(stripped)
            return

        try:
            await self._renderer.run_turn(text, images=images)
        except Exception as exc:  # noqa: BLE001
            logger.exception("run_turn failed")
            self._view.print_error(f"turn failed: {exc}")

    async def _handle_slash_command(self, text: str) -> None:
        """Parse + dispatch a slash command.

        Order of resolution:
        1. Built-in ``_cmd_*`` handler via :meth:`dispatch`
        2. Project/global custom command from
           ``.llmcode/commands/<name>.md``
        3. Loaded command skill whose ``trigger`` matches
        4. Close-match suggestion / unknown-command hint
        """
        from llm_code.cli.commands import parse_slash_command
        cmd = parse_slash_command(text)
        if cmd is None:
            return

        name = cmd.name
        args = cmd.args.strip()

        # 1. Built-in handler
        if self.dispatch(name, args):
            return

        # 2. Custom commands from .llmcode/commands/
        try:
            from llm_code.runtime.custom_commands import discover_custom_commands
            custom = discover_custom_commands(self._state.cwd)
            if name in custom:
                cmd_def = custom[name]
                rendered = cmd_def.render(args)
                self._view.print_info(f"Running custom command: /{name}")
                await self._renderer.run_turn(rendered)
                return
        except Exception as exc:  # noqa: BLE001 — non-fatal
            logger.debug("custom command lookup failed: %r", exc)

        # 3. Skill commands
        skills = self._state.skills
        if skills is not None:
            for skill in skills.command_skills:
                if skill.trigger == name:
                    self._view.print_info(f"Activated skill: {skill.name}")
                    prompt = args if args else f"Using skill: {skill.name}"
                    await self._renderer.run_turn(
                        prompt,
                        active_skill_content=skill.content,
                    )
                    return

        # 4. Unknown — suggest close match or print help hint
        self._unknown_command(name)

    def _unknown_command(self, name: str) -> None:
        """Print a close-match suggestion or the '/help' hint."""
        from difflib import get_close_matches

        from llm_code.cli.commands import KNOWN_COMMANDS
        all_names = set(KNOWN_COMMANDS)
        if self._state.skills is not None:
            all_names.update(
                s.trigger
                for s in self._state.skills.command_skills
                if s.trigger
            )
        matches = get_close_matches(name, all_names, n=1, cutoff=0.5)
        if matches:
            self._view.print_warning(
                f"Unknown command: /{name} — did you mean /{matches[0]}?"
            )
        else:
            self._view.print_warning(
                f"Unknown command: /{name} — type /help for help"
            )

    # ── sync slash dispatch ──────────────────────────────────────

    def dispatch(self, name: str, args: str) -> bool:
        """Call ``_cmd_<name>(args)`` if it exists. Return ``True`` if
        handled, ``False`` so the caller can try custom commands next.

        Mirrors the v1.x signature exactly so existing call sites
        (including test_command_dispatcher.py) transliterate cleanly.
        """
        handler = getattr(self, f"_cmd_{name}", None)
        if handler is None:
            return False
        try:
            handler(args)
        except Exception as exc:  # noqa: BLE001
            logger.exception("command /%s failed", name)
            self._view.print_error(f"/{name} failed: {exc}")
        return True

    # ── Batch A: core commands ───────────────────────────────────

    def _cmd_help(self, args: str) -> None:
        """List available slash commands and skill commands.

        v1.x showed a three-tab modal; v2.0.0 prints inline because
        the REPL backend has no modal framework. Content is the same
        — the user sees every built-in command, every loaded command
        skill, and a one-line description for each.
        """
        from llm_code.cli.commands import COMMAND_REGISTRY

        lines = [
            "llm-code understands your codebase, makes edits with your "
            "permission, and runs commands — right from your terminal.",
            "",
            "Shortcuts:",
            "  ! for bash mode          Shift+Enter for multiline",
            "  / for commands           Ctrl+D to quit",
            "  /model  switch model     /vim toggle vim mode",
            "  /plan   plan-only mode   /undo revert last edit",
            "",
            "Built-in commands:",
        ]
        for c in COMMAND_REGISTRY:
            if c.name == "quit":
                continue  # duplicate of /exit
            lines.append(f"  /{c.name:<14s} {c.description}")

        skills = self._state.skills
        if skills is not None and skills.command_skills:
            lines.append("")
            lines.append("Command skills:")
            for s in sorted(
                skills.command_skills, key=lambda x: x.trigger or x.name,
            ):
                trigger = f"/{s.trigger}" if s.trigger else f"(auto: {s.name})"
                desc = getattr(s, "description", None) or s.name
                lines.append(f"  {trigger:<14s} {desc}")

        self._view.print_info("\n".join(lines))

    def _cmd_clear(self, args: str) -> None:
        """Clear the visible area. REPL clears the terminal; bot
        backends typically no-op."""
        self._view.clear_screen()

    def _cmd_exit(self, args: str) -> None:
        """Graceful exit. Works via the ViewBackend request_exit hook
        that M10.2 added — REPL delegates to ScreenCoordinator, bot
        backends stop their webhook loop, etc."""
        self._view.request_exit()

    # /quit is an alias for /exit
    _cmd_quit = _cmd_exit

    def _cmd_cost(self, args: str) -> None:
        """Print the current session cost."""
        if self._state.cost_tracker is None:
            self._view.print_info("No cost data.")
            return
        try:
            self._view.print_info(self._state.cost_tracker.format_cost())
        except Exception as exc:  # noqa: BLE001
            self._view.print_error(f"cost tracker failed: {exc}")

    def _cmd_cancel(self, args: str) -> None:
        """Cancel the in-progress runtime turn (if the runtime supports
        it)."""
        runtime = self._state.runtime
        if runtime is not None and hasattr(runtime, "_cancel"):
            try:
                runtime._cancel()
            except Exception as exc:  # noqa: BLE001
                self._view.print_error(f"cancel failed: {exc}")
                return
        self._view.print_info("(cancelled)")

    def _cmd_cd(self, args: str) -> None:
        """Change the working directory. Without args, show the
        current directory."""
        arg = args.strip()
        if not arg:
            self._view.print_info(f"Current directory: {self._state.cwd}")
            return
        new_path = Path(arg).expanduser()
        if not new_path.is_absolute():
            new_path = self._state.cwd / new_path
        new_path = new_path.resolve()
        if not new_path.is_dir():
            self._view.print_error(f"Directory not found: {new_path}")
            return
        self._state.cwd = new_path
        try:
            os.chdir(new_path)
        except OSError as exc:
            self._view.print_error(f"chdir failed: {exc}")
            return
        self._view.print_info(f"Working directory: {new_path}")

    def _cmd_budget(self, args: str) -> None:
        """Get or set the per-session token budget."""
        arg = args.strip()
        if arg:
            try:
                self._state.budget = int(arg)
            except ValueError:
                self._view.print_error("Usage: /budget <number>")
                return
            self._view.print_info(
                f"Token budget set: {self._state.budget:,}"
            )
            return
        if self._state.budget is not None:
            self._view.print_info(
                f"Current token budget: {self._state.budget:,}"
            )
        else:
            self._view.print_info("No budget set.")

    def _cmd_plan(self, args: str) -> None:
        """Toggle plan mode — agent explores and plans without
        making changes."""
        self._state.plan_mode = not self._state.plan_mode
        if self._state.runtime is not None:
            self._state.runtime.plan_mode = self._state.plan_mode
        if self._state.plan_mode:
            self._view.print_info(
                "Plan mode ON — agent will explore and plan "
                "without making changes."
            )
        else:
            self._view.print_info("Plan mode OFF — back to normal.")

    def _cmd_yolo(self, args: str) -> None:
        """Toggle YOLO mode — auto-accept all permission prompts.

        Equivalent to v1.x ``--dangerously-skip-permissions``. The
        runtime's permission policy is mutated in place; next tool
        call bypasses the normal prompt flow.
        """
        from llm_code.runtime.permissions import PermissionMode

        runtime = self._state.runtime
        if runtime is None or getattr(runtime, "_permissions", None) is None:
            self._view.print_error("Runtime not initialized.")
            return
        policy = runtime._permissions
        current = getattr(policy, "_mode", PermissionMode.PROMPT)
        if current == PermissionMode.AUTO_ACCEPT:
            policy._mode = PermissionMode.PROMPT
            self._view.print_info(
                "YOLO mode OFF — permissions will prompt again."
            )
        else:
            policy._mode = PermissionMode.AUTO_ACCEPT
            self._view.print_warning(
                "YOLO mode ON — all permissions auto-accepted. "
                "Be careful: write/delete operations will execute "
                "without confirmation."
            )

    def _cmd_mode(self, args: str) -> None:
        """Switch between suggest/normal/plan modes."""
        from llm_code.runtime.permissions import PermissionMode

        valid_modes = {
            "suggest": (PermissionMode.PROMPT, "suggest"),
            "normal": (PermissionMode.WORKSPACE_WRITE, "normal"),
            "plan": (PermissionMode.PLAN, "plan"),
        }
        arg = args.strip().lower()
        if not arg:
            current = "plan" if self._state.plan_mode else "normal"
            self._view.print_info(
                f"Current mode: {current}\n"
                f"Available: suggest, normal, plan"
            )
            return
        if arg not in valid_modes:
            self._view.print_error(
                f"Unknown mode: {arg}. Use: suggest, normal, plan"
            )
            return
        perm_mode, label = valid_modes[arg]
        self._state.plan_mode = arg == "plan"
        runtime = self._state.runtime
        if runtime is not None:
            if hasattr(runtime, "_permissions") and runtime._permissions is not None:
                runtime._permissions._mode = perm_mode
            runtime.plan_mode = self._state.plan_mode
        self._view.print_info(f"Switched to {label} mode")

    def _cmd_thinking(self, args: str) -> None:
        """Get or set thinking mode (on/off/adaptive)."""
        mode_map = {"on": "enabled", "off": "disabled", "adaptive": "adaptive"}
        arg = args.strip().lower()
        config = self._state.config
        if arg in mode_map:
            from llm_code.runtime.config import ThinkingConfig
            if config is None:
                self._view.print_error("Config not initialized.")
                return
            current = config.thinking
            new_thinking = ThinkingConfig(
                mode=mode_map[arg],
                budget_tokens=current.budget_tokens,
            )
            self._state.config = dataclasses.replace(
                config, thinking=new_thinking,
            )
            if self._state.runtime is not None:
                self._state.runtime._config = self._state.config
            self._view.print_info(f"Thinking mode: {mode_map[arg]}")
            return
        current_mode = (
            config.thinking.mode if config is not None else "unknown"
        )
        self._view.print_info(
            f"Thinking: {current_mode}\n"
            "Usage: /thinking [adaptive|on|off]"
        )

    def _cmd_gain(self, args: str) -> None:
        """Print token-usage report from the TokenTracker."""
        try:
            from llm_code.tools.token_tracker import TokenTracker
            days = int(args) if args.strip().isdigit() else 30
            tracker = TokenTracker()
            try:
                report = tracker.format_report(days)
            finally:
                try:
                    tracker.close()
                except Exception:
                    pass
            self._view.print_info(report)
        except Exception as exc:  # noqa: BLE001
            self._view.print_error(f"gain report failed: {exc}")

    def _cmd_profile(self, args: str) -> None:
        """Show per-model token/cost breakdown from the query profiler."""
        runtime = self._state.runtime
        profiler = (
            getattr(runtime, "_query_profiler", None)
            if runtime is not None
            else None
        )
        if profiler is None:
            self._view.print_info("(profiler not initialized)")
            return
        pricing = getattr(self._state.config, "pricing", None)
        try:
            self._view.print_info(profiler.format_breakdown(pricing))
        except Exception as exc:  # noqa: BLE001
            self._view.print_error(f"profiler failed: {exc}")

    def _cmd_diff(self, args: str) -> None:
        """Show the git diff since the last checkpoint."""
        mgr = self._state.checkpoint_mgr
        if mgr is None or not mgr.can_undo():
            self._view.print_info("No checkpoints available.")
            return
        try:
            last_cp = mgr.list_checkpoints()[-1]
        except Exception as exc:  # noqa: BLE001
            self._view.print_error(f"checkpoint list failed: {exc}")
            return
        try:
            result = subprocess.run(
                ["git", "diff", last_cp.git_sha, "HEAD"],
                capture_output=True,
                text=True,
                cwd=self._state.cwd,
                check=False,
            )
        except Exception as exc:  # noqa: BLE001
            self._view.print_error(f"git diff failed: {exc}")
            return
        output = result.stdout.strip()
        if output:
            self._view.print_info(f"```diff\n{output}\n```")
        else:
            self._view.print_info("No changes since last checkpoint.")


__all__ = ["CommandDispatcher"]
