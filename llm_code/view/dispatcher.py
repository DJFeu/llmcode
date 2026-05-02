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

        # 1. Built-in handler (async-first, then sync fallback)
        if await self.dispatch_async(name, args):
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

    async def dispatch_async(self, name: str, args: str) -> bool:
        """Like :meth:`dispatch` but awaits coroutine handlers.

        M15 introduced async command handlers (e.g. ``_acmd_skill``)
        that need to ``await show_select`` from the dialog popover.
        ``_handle_slash_command`` (which is already async) calls this
        instead of :meth:`dispatch` so async handlers work cleanly.
        Sync ``_cmd_*`` handlers still work — they're called normally.
        """
        # Prefer async handler (_acmd_*) over sync (_cmd_*)
        async_handler = getattr(self, f"_acmd_{name}", None)
        if async_handler is not None:
            try:
                await async_handler(args)
            except Exception as exc:  # noqa: BLE001
                logger.exception("command /%s failed", name)
                self._view.print_error(f"/{name} failed: {exc}")
            return True
        # Fall back to sync handler
        return self.dispatch(name, args)

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
        current = policy.mode
        # Route through switch_to so the ModeTransition event is
        # recorded for the SystemPromptBuilder to consume (and emit
        # the build-switch reminder where applicable).
        if current == PermissionMode.AUTO_ACCEPT:
            policy.switch_to(PermissionMode.PROMPT)
            self._view.print_info(
                "YOLO mode OFF — permissions will prompt again."
            )
        else:
            policy.switch_to(PermissionMode.AUTO_ACCEPT)
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
                # Route through switch_to so the ModeTransition event is
                # recorded — SystemPromptBuilder reads it on the next
                # turn to inject the build-switch reminder when the
                # flip relaxes the read-only constraint.
                runtime._permissions.switch_to(perm_mode)
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

    # ── Batch B: runtime / config / state mutation ───────────────

    def _cmd_compact(self, args: str) -> None:
        """Manually compact the conversation to free context space.

        Args: optional integer, number of recent messages to keep
        verbatim (default 4).
        """
        runtime = self._state.runtime
        if runtime is None:
            self._view.print_error("Compaction unavailable: runtime not initialized.")
            return
        try:
            from llm_code.runtime.compaction import compact_session
            before_msgs = len(runtime.session.messages)
            before_toks = runtime.session.estimated_tokens()
            keep = 4
            if args.strip():
                try:
                    keep = int(args.strip())
                except ValueError:
                    keep = 4
            runtime.session = compact_session(
                runtime.session,
                keep_recent=keep,
                summary="(manual /compact)",
            )
            after_msgs = len(runtime.session.messages)
            after_toks = runtime.session.estimated_tokens()
            self._state.context_warned = False
            self._view.print_info(
                f"Compacted: {before_msgs} → {after_msgs} messages, "
                f"~{before_toks:,} → ~{after_toks:,} tokens. "
                "Older messages summarized."
            )
        except Exception as exc:  # noqa: BLE001
            self._view.print_error(f"Compaction failed: {exc}")

    def _cmd_export(self, args: str) -> None:
        """Export the live session to a Markdown file.

        Usage:
            /export             → ./llmcode-export-<id>-<timestamp>.md
            /export <path>      → write to the given path
        """
        from datetime import datetime

        runtime = self._state.runtime
        if runtime is None or getattr(runtime, "session", None) is None:
            self._view.print_error("Export unavailable: no active session.")
            return
        session = runtime.session
        if not session.messages:
            self._view.print_info("Nothing to export — conversation is empty.")
            return

        target_arg = args.strip()
        if target_arg:
            target = Path(target_arg).expanduser()
            if not target.is_absolute():
                target = self._state.cwd / target
        else:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            default_name = f"llmcode-export-{session.id}-{stamp}.md"
            target = self._state.cwd / default_name

        try:
            from llm_code.view.session_export import render_session_markdown
            markdown = render_session_markdown(session)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(markdown, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            self._view.print_error(f"Export failed: {exc}")
            return
        self._view.print_info(
            f"Exported {len(session.messages)} messages → {target}"
        )

    def _cmd_undo(self, args: str) -> None:
        """Git-based undo of the last N tool operations."""
        mgr = self._state.checkpoint_mgr
        if mgr is None:
            self._view.print_info(
                "Not in a git repository — undo not available."
            )
            return
        if args.strip() == "list":
            try:
                cps = mgr.list_checkpoints()
            except Exception as exc:  # noqa: BLE001
                self._view.print_error(f"checkpoint list failed: {exc}")
                return
            if not cps:
                self._view.print_info("No checkpoints.")
                return
            lines = [
                f"  {cp.id}  {cp.tool_name}  {cp.timestamp[:19]}"
                for cp in cps
            ]
            self._view.print_info("\n".join(lines))
            return
        if not mgr.can_undo():
            self._view.print_info("Nothing to undo.")
            return
        steps = 1
        if args.strip().isdigit():
            steps = int(args.strip())
        try:
            cp = mgr.undo(steps)
        except Exception as exc:  # noqa: BLE001
            self._view.print_error(f"undo failed: {exc}")
            return
        if cp is None:
            self._view.print_info("Nothing to undo.")
            return
        label = f"Undone {steps} step(s)" if steps > 1 else "Undone"
        self._view.print_info(
            f"{label}: {cp.tool_name} ({cp.tool_args_summary[:50]})"
        )

    def _cmd_model(self, args: str) -> None:
        """Show or switch the active model.

        - ``/model`` with no args → show current + profile info
        - ``/model route`` → list configured model routing
        - ``/model <name>`` → switch the config and runtime model
          used by subsequent turns.
        """
        arg = args.strip()
        config = self._state.config
        if arg == "route":
            self._show_model_routes()
            return
        if arg:
            if config is None:
                self._view.print_error("Config not initialized.")
                return
            self._state.config = dataclasses.replace(config, model=arg)
            if self._state.runtime is not None:
                self._apply_runtime_model_switch(self._state.runtime, arg)
            self._view.print_info(f"Model switched to: {arg}")
            self._view.print_info(self._format_profile_info(arg))
            return
        model = config.model if config is not None else "(not set)"
        self._view.print_info(f"Current model: {model}")
        if model and model != "(not set)":
            self._view.print_info(self._format_profile_info(model))

    def _apply_runtime_model_switch(self, runtime: object, model: str) -> None:
        switch_model = getattr(runtime, "switch_model", None)
        if callable(switch_model):
            switch_model(model, self._state.config)
            return

        setattr(runtime, "_config", self._state.config)
        setattr(runtime, "_active_model", model)
        try:
            from llm_code.runtime.model_profile import get_profile
            setattr(runtime, "_model_profile", get_profile(model))
        except Exception:  # noqa: BLE001
            pass
        if hasattr(runtime, "_force_xml_mode"):
            delattr(runtime, "_force_xml_mode")

    def _format_profile_info(self, model: str) -> str:
        """Format a model profile as a compact info string."""
        try:
            from llm_code.runtime.model_profile import get_profile
            p = get_profile(model)
        except Exception as exc:  # noqa: BLE001
            return f"(profile lookup failed: {exc})"
        parts = [f"Profile: {p.name or model}"]
        caps = []
        if p.native_tools:
            caps.append("native-tools")
        if p.supports_reasoning:
            caps.append("reasoning")
        if p.supports_images:
            caps.append("images")
        if p.force_xml_tools:
            caps.append("xml-tools")
        if p.implicit_thinking:
            caps.append("implicit-thinking")
        if p.is_local:
            caps.append("local")
        if caps:
            parts.append(f"  Capabilities: {', '.join(caps)}")
        parts.append(
            f"  Provider: {p.provider_type}  |  "
            f"Context: {p.context_window:,}  |  "
            f"Max output: {p.max_output_tokens:,}"
        )
        if p.price_input > 0 or p.price_output > 0:
            parts.append(
                f"  Pricing: ${p.price_input:.2f}/${p.price_output:.2f} "
                f"per 1M tokens"
            )
        return "\n".join(parts)

    def _show_model_routes(self) -> None:
        """List the configured model-routing table."""
        cfg = self._state.config
        if cfg is None:
            self._view.print_info("No config loaded.")
            return
        routes: list[str] = []
        if hasattr(cfg, "model") and cfg.model:
            routes.append(f"  {'default':<12s}  {cfg.model}")
        if hasattr(cfg, "model_routing") and cfg.model_routing:
            mr = cfg.model_routing
            for attr in ("sub_agent", "compaction", "fallback"):
                model = getattr(mr, attr, None)
                if model:
                    routes.append(f"  {attr:<12s}  {model}")
        if routes:
            self._view.print_info("Model routing:\n" + "\n".join(routes))
        else:
            self._view.print_info("No model routing configured")

    def _cmd_cache(self, args: str) -> None:
        """Manage persistent caches (server capabilities + skill router)."""
        sub = args.strip().lower().split()[0] if args.strip() else "list"
        if sub == "list":
            lines: list[str] = ["Persistent caches:", ""]
            try:
                from llm_code.runtime.server_capabilities import (
                    _CACHE_PATH as _sc_path,
                )
                if _sc_path.exists():
                    import json as _json
                    data = _json.loads(_sc_path.read_text(encoding="utf-8"))
                    lines.append(f"server_capabilities ({_sc_path}):")
                    for key, entry in data.items():
                        native = entry.get("native_tools", "?")
                        cached_at = entry.get("cached_at", "?")
                        lines.append(
                            f"  {key} → native_tools={native} "
                            f"(cached {cached_at})"
                        )
                else:
                    lines.append("server_capabilities: (no cache file)")
            except Exception as exc:  # noqa: BLE001
                lines.append(f"server_capabilities: error reading: {exc}")
            try:
                from llm_code.runtime.skill_router_cache import (
                    _CACHE_PATH as _src_path,
                )
                if _src_path.exists():
                    import json as _json2
                    data2 = _json2.loads(
                        _src_path.read_text(encoding="utf-8")
                    )
                    total_entries = sum(
                        len(bucket.get("entries", {}))
                        for bucket in data2.values()
                        if isinstance(bucket, dict)
                    )
                    lines.append(
                        f"\nskill_router_cache ({_src_path}): "
                        f"{total_entries} entries across {len(data2)} skill set(s)"
                    )
                else:
                    lines.append("\nskill_router_cache: (no cache file)")
            except Exception as exc:  # noqa: BLE001
                lines.append(f"\nskill_router_cache: error reading: {exc}")
            self._view.print_info("\n".join(lines))
            return
        if sub == "clear":
            cleared: list[str] = []
            try:
                from llm_code.runtime.server_capabilities import (
                    clear_native_tools_cache,
                )
                clear_native_tools_cache()
                cleared.append("server_capabilities")
            except Exception:
                pass
            try:
                from llm_code.runtime.skill_router_cache import clear_cache
                clear_cache()
                cleared.append("skill_router_cache")
            except Exception:
                pass
            runtime = self._state.runtime
            if runtime is not None:
                if hasattr(runtime, "_force_xml_mode"):
                    runtime._force_xml_mode = False
                if (
                    hasattr(runtime, "_skill_router")
                    and runtime._skill_router is not None
                    and hasattr(runtime._skill_router, "_cache")
                ):
                    runtime._skill_router._cache.clear()
            self._view.print_info(
                f"Cleared: {', '.join(cleared) or 'nothing'}. "
                "In-memory caches reset. Next turn will re-probe "
                "server capabilities and re-run skill routing."
            )
            return
        if sub == "probe":
            try:
                from llm_code.runtime.server_capabilities import (
                    clear_native_tools_cache,
                )
                clear_native_tools_cache()
            except Exception:
                pass
            runtime = self._state.runtime
            if runtime is not None and hasattr(runtime, "_force_xml_mode"):
                runtime._force_xml_mode = False
            self._view.print_info(
                "Server capabilities cache cleared. Next turn will "
                "re-probe native tool support."
            )
            return
        self._view.print_info("Usage: /cache list | /cache clear | /cache probe")

    def _cmd_theme(self, args: str) -> None:
        """v16 M4 — switch the active Rich theme.

        ``/theme`` (no arg)        → list available themes with the active marked
        ``/theme <name>``         → switch + persist to config.ui_theme
        ``/theme <unknown>``      → error with available list
        """
        from llm_code.view.themes import (
            apply_theme_to_palette,
            list_theme_names,
        )

        names = list_theme_names()
        cfg = self._state.config
        active = "default"
        if cfg is not None:
            active = (
                getattr(cfg, "ui_theme", None)
                or getattr(cfg, "theme_name", None)
                or "default"
            )

        arg = args.strip()
        if not arg:
            lines = ["Themes:"]
            for name in names:
                marker = "* " if name == active else "  "
                lines.append(f"{marker}{name}")
            self._view.print_info("\n".join(lines))
            return

        if arg not in names:
            self._view.print_error(
                f"Unknown theme: {arg!r}. Available: {', '.join(names)}"
            )
            return

        palette = apply_theme_to_palette(arg)
        if palette is None:
            # apply_theme_to_palette already logged the warning.
            self._view.print_error(f"Failed to apply theme {arg!r}")
            return

        # Persist the choice on the config object. Use _patched_setattr
        # if frozen, otherwise plain assignment via dataclasses.replace.
        if cfg is not None:
            try:
                import dataclasses as _dc
                if _dc.is_dataclass(cfg) and getattr(
                    type(cfg), "__dataclass_params__", None,
                ) and type(cfg).__dataclass_params__.frozen:
                    self._state.config = _dc.replace(cfg, ui_theme=arg)
                else:
                    setattr(cfg, "ui_theme", arg)
            except Exception:  # noqa: BLE001
                # Persistence is best-effort — in-memory swap already
                # happened.
                pass

        # Trigger a redraw via the coordinator if available.
        coordinator = getattr(self._view, "coordinator", None)
        if coordinator is not None and hasattr(coordinator, "_app"):
            try:
                app = getattr(coordinator, "_app", None)
                if app is not None and getattr(app, "is_running", False):
                    app.invalidate()
            except Exception:  # noqa: BLE001
                pass

        logger.info("theme_set name=%s", arg)
        self._view.print_info(f"Theme set to {arg!r}.")

    def _cmd_config(self, args: str) -> None:
        """Print the active config summary."""
        cfg = self._state.config
        if cfg is None:
            self._view.print_info("No config loaded.")
            return
        lines = [
            f"model: {cfg.model}",
            f"provider: {cfg.provider_base_url or 'default'}",
            f"permission: {cfg.permission_mode}",
            f"thinking: {cfg.thinking.mode}",
        ]
        self._view.print_info("\n".join(lines))

    def _cmd_set(self, args: str) -> None:
        """Set a config field: ``/set <key> <value>``."""
        parts = args.strip().split(None, 1)
        if len(parts) < 2:
            try:
                from llm_code.view.settings import editable_fields
                fields = ", ".join(sorted(editable_fields()))
                self._view.print_info(
                    f"Usage: /set <key> <value>\nEditable: {fields}"
                )
            except Exception:
                self._view.print_info("Usage: /set <key> <value>")
            return
        key, value = parts[0], parts[1]
        cfg = self._state.config
        if cfg is None:
            self._view.print_error("Config not initialized.")
            return
        try:
            from llm_code.view.settings import apply_setting
            self._state.config = apply_setting(cfg, key, value)
            if self._state.runtime is not None:
                self._state.runtime._config = self._state.config
            self._view.print_info(f"Set {key} = {value}")
        except ValueError as exc:
            self._view.print_error(f"{exc}")

    def _cmd_settings(self, args: str) -> None:
        """Print the settings summary.

        v1.x opened a Textual modal; v2.0.0 REPL prints the same
        content inline. Interactive editing happens via ``/set``.
        """
        cfg = self._state.config
        if cfg is None:
            self._view.print_info("No config loaded.")
            return
        try:
            from llm_code.view.settings import (
                build_settings_sections,
                render_sections_text,
            )
        except Exception as exc:  # noqa: BLE001
            self._view.print_error(f"settings helper import failed: {exc}")
            return
        # Build a minimal "runtime_like" object matching the helper's
        # duck-typed expectations.
        from types import SimpleNamespace as _NS
        runtime_like = _NS(
            model=getattr(cfg, "model", ""),
            permission_mode=getattr(cfg, "permission_mode", ""),
            plan_mode=self._state.plan_mode,
            config=cfg,
            cost_tracker=self._state.cost_tracker,
            keybindings=None,
            active_skills=[],
        )
        try:
            sections = build_settings_sections(runtime_like)
            body = render_sections_text(sections)
        except Exception as exc:  # noqa: BLE001
            self._view.print_error(f"settings render failed: {exc}")
            return
        self._view.print_info(body)
        self._view.print_info(
            "(v2.0.0: edit via /set <key> <value>, not an interactive modal.)"
        )

    def _cmd_init(self, args: str) -> None:
        """Run an LLM-driven analysis to generate AGENTS.md.

        Reads the init template from the package's ``cli/templates``
        directory, substitutes ``$ARGUMENTS``, and runs the result
        through ``renderer.run_turn``. Scheduled as an asyncio task
        so dispatch() can return to its caller immediately — the
        actual LLM turn happens on the event loop.
        """
        template_path = (
            Path(__file__).parent.parent / "cli" / "templates" / "init.md"
        )
        if not template_path.is_file():
            self._view.print_error(f"Init template not found: {template_path}")
            return
        try:
            template = template_path.read_text(encoding="utf-8")
        except OSError as exc:
            self._view.print_error(f"Failed to read init template: {exc}")
            return
        prompt = template.replace("$ARGUMENTS", args.strip() or "(none)")
        self._view.print_info("Analyzing repo and generating AGENTS.md…")
        self._schedule_renderer(prompt)

    def _cmd_index(self, args: str) -> None:
        """Show or rebuild the project index."""
        if args.strip() == "rebuild":
            try:
                from llm_code.runtime.indexer import ProjectIndexer
                self._state.project_index = ProjectIndexer(
                    self._state.cwd
                ).build_index()
                idx = self._state.project_index
                self._view.print_info(
                    f"Index rebuilt: {len(idx.files)} files, "
                    f"{len(idx.symbols)} symbols"
                )
            except Exception as exc:  # noqa: BLE001
                self._view.print_error(f"Index rebuild failed: {exc}")
            return
        idx = self._state.project_index
        if idx is not None:
            lines = [
                f"Files: {len(idx.files)}, Symbols: {len(idx.symbols)}"
            ]
            for s in idx.symbols[:20]:
                lines.append(f"  {s.kind} {s.name} — {s.file}:{s.line}")
            self._view.print_info("\n".join(lines))
        else:
            self._view.print_info("No index available.")

    def _cmd_harness(self, args: str) -> None:
        """Show or configure the runtime harness (guides + sensors)."""
        runtime = self._state.runtime
        harness = getattr(runtime, "_harness", None) if runtime else None
        if harness is None:
            self._view.print_info("Harness not available.")
            return
        parts = args.strip().split()
        if not parts:
            try:
                status = harness.status()
            except Exception as exc:  # noqa: BLE001
                self._view.print_error(f"harness status failed: {exc}")
                return
            lines = [f"Harness: {status['template']}", "", "  Guides:"]
            for g in status["guides"]:
                mark = "✓" if g["enabled"] else "✗"
                lines.append(
                    f"    {mark} {g['name']:<22} {g['trigger']:<12} {g['kind']}"
                )
            lines.append("")
            lines.append("  Sensors:")
            for s in status["sensors"]:
                mark = "✓" if s["enabled"] else "✗"
                lines.append(
                    f"    {mark} {s['name']:<22} {s['trigger']:<12} {s['kind']}"
                )
            self._view.print_info("\n".join(lines))
            return
        action = parts[0]
        try:
            if action == "enable" and len(parts) > 1:
                harness.enable(parts[1])
                self._view.print_info(f"Enabled: {parts[1]}")
            elif action == "disable" and len(parts) > 1:
                harness.disable(parts[1])
                self._view.print_info(f"Disabled: {parts[1]}")
            elif action == "template" and len(parts) > 1:
                from llm_code.harness.config import HarnessConfig
                from llm_code.harness.templates import default_controls
                new_controls = default_controls(parts[1])
                harness._config = HarnessConfig(
                    template=parts[1], controls=new_controls,
                )
                harness._overrides.clear()
                self._view.print_info(f"Switched to template: {parts[1]}")
            else:
                self._view.print_info(
                    "Usage: /harness [enable|disable|template] [name]"
                )
        except Exception as exc:  # noqa: BLE001
            self._view.print_error(f"harness command failed: {exc}")

    def _cmd_session(self, args: str) -> None:
        """Print a short session hint.

        Full session management (list/save/load) is exposed via the
        /checkpoint command and the session tools. This is the M10
        parity port of v1.x's no-op stub.
        """
        self._view.print_info(
            "Session management: use /checkpoint list | save | "
            "resume [session_id]"
        )

    def _cmd_checkpoint(self, args: str) -> None:
        """Save, list, or resume a runtime checkpoint."""
        try:
            from llm_code.runtime.checkpoint_recovery import CheckpointRecovery
        except ImportError:
            self._view.print_error("Checkpoint recovery not available.")
            return
        checkpoints_dir = Path.home() / ".llmcode" / "checkpoints"
        recovery = CheckpointRecovery(checkpoints_dir)
        parts = args.strip().split(None, 1)
        sub = parts[0].lower() if parts else "list"
        rest = parts[1].strip() if len(parts) > 1 else ""
        runtime = self._state.runtime
        if sub == "save":
            if runtime is None:
                self._view.print_error("No active session to checkpoint.")
                return
            try:
                path = recovery.save_checkpoint(runtime.session)
                self._view.print_info(f"Checkpoint saved: {path}")
            except Exception as exc:  # noqa: BLE001
                self._view.print_error(f"Save failed: {exc}")
            return
        if sub in ("list", ""):
            try:
                entries = recovery.list_checkpoints()
            except Exception as exc:  # noqa: BLE001
                self._view.print_error(f"List failed: {exc}")
                return
            if not entries:
                self._view.print_info("No checkpoints found.")
                return
            lines = ["Checkpoints:"]
            for e in entries:
                lines.append(
                    f"  {e['session_id']}  {e['saved_at'][:19]}  "
                    f"({e['message_count']} msgs)  {e['project_path']}"
                )
            self._view.print_info("\n".join(lines))
            return
        if sub == "resume":
            cost_tracker = self._state.cost_tracker
            try:
                if rest:
                    session = recovery.load_checkpoint(
                        rest, cost_tracker=cost_tracker,
                    )
                else:
                    session = recovery.detect_last_checkpoint(
                        cost_tracker=cost_tracker,
                    )
            except Exception as exc:  # noqa: BLE001
                self._view.print_error(f"Resume failed: {exc}")
                return
            if session is None:
                self._view.print_info("No checkpoint found to resume.")
                return
            if runtime is not None:
                runtime.session = session
            self._view.print_info(
                f"Resumed session {session.id} "
                f"({len(session.messages)} messages)"
            )
            return
        self._view.print_info(
            "Usage: /checkpoint [save|list|resume [session_id]]"
        )

    def _cmd_update(self, args: str) -> None:
        """Check for llmcode updates; schedule the check on the
        running asyncio loop so dispatch() returns immediately."""
        async def _do_update() -> None:
            try:
                from llm_code.cli.updater import check_update, run_upgrade
            except Exception as exc:  # noqa: BLE001
                self._view.print_error(f"updater import failed: {exc}")
                return
            self._view.print_info("Checking for updates…")
            try:
                info = await check_update()
            except Exception as exc:  # noqa: BLE001
                self._view.print_error(f"update check failed: {exc}")
                return
            if info is None:
                self._view.print_info("Already on the latest version.")
                return
            current, latest = info
            self._view.print_info(
                f"Update available: {current} → {latest}\n"
                f"Running: pip install --upgrade llmcode-cli"
            )
            try:
                ok, output = await run_upgrade()
            except Exception as exc:  # noqa: BLE001
                self._view.print_error(f"upgrade failed: {exc}")
                return
            if ok:
                self._view.print_info(
                    f"Updated to {latest}. Restart llmcode to use "
                    "the new version."
                )
            else:
                self._view.print_error(f"Update failed:\n{output}")

        self._schedule_coroutine(_do_update())

    # ── helpers for async scheduling ─────────────────────────────

    def _schedule_renderer(
        self,
        prompt: str,
        images: Optional[list] = None,
        active_skill_content: Optional[str] = None,
    ) -> None:
        """Fire-and-forget a renderer turn from a sync command handler.

        Used by commands like /init that want to trigger an LLM turn
        as a side effect but can't block dispatch(). Schedules the
        coroutine on the running event loop; errors are logged but
        don't propagate because dispatch() has already returned.
        """
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Not inside an async context — run to completion. This
            # path is used by tests that call dispatch() outside an
            # event loop.
            asyncio.run(
                self._renderer.run_turn(
                    prompt,
                    images=images,
                    active_skill_content=active_skill_content,
                )
            )
            return
        loop.create_task(
            self._renderer.run_turn(
                prompt,
                images=images,
                active_skill_content=active_skill_content,
            )
        )

    def _schedule_coroutine(self, coro) -> None:
        """Fire-and-forget any coroutine from a sync command handler.

        Errors are swallowed and logged — by the time a fire-and-forget
        task finishes, dispatch() has long since returned, so exceptions
        have no upstream caller to receive them.
        """
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(coro)
            return
        loop.create_task(coro)

    # ── Batch C: feature-module commands ─────────────────────────

    def _cmd_search(self, args: str) -> None:
        """Cross-session FTS5 search + current-session fallback."""
        if not args:
            self._view.print_info("Usage: /search <query>")
            return
        lines: list[str] = []
        try:
            from llm_code.runtime.conversation_db import ConversationDB
            db = ConversationDB()
            safe_query = self._escape_fts5(args)
            db_results = db.search(safe_query, limit=20)
            for r in db_results:
                session_label = (
                    r.conversation_name or r.conversation_id[:8]
                )
                date_str = r.created_at[:10] if r.created_at else ""
                snippet = (
                    r.content_snippet
                    .replace(">>>", "**")
                    .replace("<<<", "**")
                )
                role_icon = ">" if r.role == "user" else "<"
                lines.append(
                    f"  {role_icon} [{date_str}] ({session_label}) {snippet}"
                )
            db.close()
        except Exception:
            pass

        if not lines and self._state.runtime is not None:
            for msg in self._state.runtime.session.messages:
                text = " ".join(
                    getattr(b, "text", "")
                    for b in msg.content
                    if hasattr(b, "text")
                )
                if args.lower() in text.lower():
                    role_icon = ">" if msg.role == "user" else "<"
                    lines.append(f"  {role_icon} [current] {text[:120]}")

        if lines:
            header = f"Found {len(lines)} match(es) for \"{args}\""
            if len(lines) > 20:
                header += " (showing first 20)"
            self._view.print_info(header + ":\n" + "\n".join(lines[:20]))
        else:
            self._view.print_info(f"No matches for: {args}")

    @staticmethod
    def _escape_fts5(query: str) -> str:
        """Escape special FTS5 characters for safe query strings."""
        tokens = query.split()
        if not tokens:
            return query
        return " ".join(f'"{t}"' for t in tokens)

    @staticmethod
    def _is_safe_name(name: str) -> bool:
        """Validate skill/plugin name: alphanumeric + ``-_.`` only."""
        import re
        return bool(re.match(r"^[a-zA-Z0-9_.-]+$", name))

    @staticmethod
    def _is_valid_repo(source: str) -> bool:
        """Validate ``owner/repo`` format with safe characters only."""
        import re
        cleaned = source.replace("https://github.com/", "").rstrip("/")
        parts = cleaned.split("/")
        if len(parts) != 2:
            return False
        return all(re.match(r"^[a-zA-Z0-9_.-]+$", p) for p in parts)

    def _cmd_knowledge(self, args: str) -> None:
        """View or rebuild the project knowledge base."""
        parts = args.strip().split()
        action = parts[0] if parts else ""
        if action == "rebuild":
            self._schedule_coroutine(self._rebuild_knowledge())
            return
        try:
            from llm_code.runtime.knowledge_compiler import KnowledgeCompiler
            compiler = KnowledgeCompiler(
                cwd=self._state.cwd, llm_provider=None,
            )
            entries = compiler.get_index()
        except Exception:
            self._view.print_info("Knowledge base not available.")
            return
        if not entries:
            self._view.print_info(
                "Knowledge base is empty.\n"
                "Run /knowledge rebuild to build it now."
            )
            return
        lines = ["Project Knowledge Base:", ""]
        for entry in entries:
            lines.append(f"- {entry.title} — {entry.summary}")
        lines.append(
            f"\n{len(entries)} articles. "
            "Use /knowledge rebuild to force recompilation."
        )
        self._view.print_info("\n".join(lines))

    async def _rebuild_knowledge(self) -> None:
        runtime = self._state.runtime
        if runtime is None:
            self._view.print_error("Runtime not available.")
            return
        self._view.print_info("Rebuilding knowledge base…")
        try:
            from llm_code.runtime.knowledge_compiler import KnowledgeCompiler
            compile_model = ""
            cfg = self._state.config
            if cfg is not None:
                if hasattr(cfg, "knowledge"):
                    compile_model = cfg.knowledge.compile_model
                if not compile_model and hasattr(cfg, "model_routing"):
                    compile_model = cfg.model_routing.compaction
            compiler = KnowledgeCompiler(
                cwd=self._state.cwd,
                llm_provider=runtime._provider,
                compile_model=compile_model,
            )
            ingest_data = compiler.ingest(facts=[], since_commit=None)
            import asyncio
            await asyncio.wait_for(compiler.compile(ingest_data), timeout=60.0)
            entries = compiler.get_index()
            self._view.print_info(
                f"Knowledge base rebuilt: {len(entries)} articles."
            )
        except Exception as exc:  # noqa: BLE001
            self._view.print_error(f"Rebuild failed: {exc}")

    def _cmd_dump(self, args: str) -> None:
        """Dump the codebase to a file for external LLM use."""
        self._schedule_coroutine(self._run_dump(args))

    async def _run_dump(self, args: str) -> None:
        try:
            from llm_code.tools.dump import dump_codebase
        except Exception as exc:  # noqa: BLE001
            self._view.print_error(f"dump import failed: {exc}")
            return
        max_files = 200
        if args.strip().isdigit():
            max_files = int(args.strip())
        try:
            result = dump_codebase(self._state.cwd, max_files=max_files)
        except Exception as exc:  # noqa: BLE001
            self._view.print_error(f"dump failed: {exc}")
            return
        if result.file_count == 0:
            self._view.print_info("No source files found to dump.")
            return
        dump_path = self._state.cwd / ".llmcode" / "dump.txt"
        try:
            dump_path.parent.mkdir(parents=True, exist_ok=True)
            dump_path.write_text(result.text, encoding="utf-8")
        except OSError as exc:
            self._view.print_error(f"dump write failed: {exc}")
            return
        self._view.print_info(
            f"Dumped {result.file_count} files "
            f"({result.total_lines:,} lines, "
            f"~{result.estimated_tokens:,} tokens)\n"
            f"Saved to: {dump_path}"
        )

    def _cmd_analyze(self, args: str) -> None:
        """Run code analysis rules on the codebase."""
        self._schedule_coroutine(self._run_analyze(args))

    async def _run_analyze(self, args: str) -> None:
        try:
            from llm_code.analysis.engine import run_analysis
        except Exception as exc:  # noqa: BLE001
            self._view.print_error(f"analysis import failed: {exc}")
            return
        target = Path(args.strip()) if args.strip() else self._state.cwd
        if not target.is_absolute():
            target = self._state.cwd / target
        try:
            result = run_analysis(target)
        except Exception as exc:  # noqa: BLE001
            self._view.print_error(f"Analysis failed: {exc}")
            return
        self._view.print_info(result.format_chat())
        if result.violations:
            ctx = result.format_context(max_tokens=1000)
            self._state.analysis_context = ctx
            if self._state.runtime is not None:
                self._state.runtime.analysis_context = ctx
        else:
            self._state.analysis_context = None
            if self._state.runtime is not None:
                self._state.runtime.analysis_context = None

    def _cmd_diff_check(self, args: str) -> None:
        """Show new/fixed violations since the last analysis run."""
        self._schedule_coroutine(self._run_diff_check(args))

    async def _run_diff_check(self, args: str) -> None:
        try:
            from llm_code.analysis.engine import run_diff_check
        except Exception as exc:  # noqa: BLE001
            self._view.print_error(f"diff_check import failed: {exc}")
            return
        try:
            new_v, fixed_v = run_diff_check(self._state.cwd)
        except Exception as exc:  # noqa: BLE001
            self._view.print_error(f"Diff check failed: {exc}")
            return
        if not new_v and not fixed_v:
            self._view.print_info(
                "No changes in violations since last analysis."
            )
            return
        lines = ["Diff Check"]
        for v in new_v:
            loc = f"{v.file_path}:{v.line}" if v.line > 0 else v.file_path
            lines.append(f"NEW {v.severity.upper()} {loc} {v.message}")
        for v in fixed_v:
            loc = f"{v.file_path}:{v.line}" if v.line > 0 else v.file_path
            lines.append(f"FIXED {v.severity.upper()} {loc} {v.message}")
        lines.append(f"\n{len(new_v)} new, {len(fixed_v)} fixed")
        self._view.print_info("\n".join(lines))

    def _cmd_memory(self, args: str) -> None:
        """Legacy key-value memory store sub-command router."""
        mem = self._state.memory
        if mem is None:
            self._view.print_info("Memory not initialized.")
            return
        parts = args.strip().split(None, 2)
        sub = parts[0] if parts else ""
        try:
            if sub == "set" and len(parts) > 2:
                mem.store(parts[1], parts[2])
                self._view.print_info(f"Stored: {parts[1]}")
                return
            if sub == "get" and len(parts) > 1:
                val = mem.recall(parts[1])
                if val:
                    self._view.print_info(str(val))
                else:
                    self._view.print_info(f"Key not found: {parts[1]}")
                return
            if sub == "delete" and len(parts) > 1:
                mem.delete(parts[1])
                self._view.print_info(f"Deleted: {parts[1]}")
                return
            if sub == "history":
                summaries = mem.load_consolidated_summaries(limit=5)
                if not summaries:
                    self._view.print_info("No consolidated memories yet.")
                    return
                lines = [f"Consolidated Memories ({len(summaries)} most recent)"]
                for i, s in enumerate(summaries):
                    preview = "\n".join(s.strip().splitlines()[:3])
                    lines.append(f"  #{i+1} {preview}")
                self._view.print_info("\n".join(lines))
                return
            if sub == "lint":
                self._memory_lint_fast()
                return
            if sub == "consolidate":
                self._view.print_info(
                    "Use --lite mode for consolidate (requires async)."
                )
                return
            # default: list all entries
            entries = mem.get_all()
            lines = [f"Memory ({len(entries)} entries)"]
            for k, v in entries.items():
                lines.append(f"  {k}: {v.value[:60]}")
            if not entries:
                lines.append("  No memories stored.")
            self._view.print_info("\n".join(lines))
        except Exception as exc:  # noqa: BLE001
            self._view.print_error(f"Memory error: {exc}")

    def _memory_lint_fast(self) -> None:
        """Run fast computational memory lint."""
        mem = self._state.memory
        if mem is None:
            return
        try:
            from llm_code.runtime.memory_validator import lint_memory
            result = lint_memory(memory_dir=mem._dir, cwd=self._state.cwd)
            report = result.format_report()
            if not result.stale and not result.coverage_gaps and not result.old:
                report += (
                    "\n\nContradictions: (requires LLM, skipped — "
                    "use /memory lint --deep)"
                )
            self._view.print_info(report)
        except Exception as exc:  # noqa: BLE001
            self._view.print_error(f"Lint failed: {exc}")

    def _cmd_map(self, args: str) -> None:
        """Show a compact repo map for the current project."""
        try:
            from llm_code.runtime.repo_map import build_repo_map
            repo_map = build_repo_map(self._state.cwd)
            compact = repo_map.to_compact(max_tokens=2000)
        except Exception as exc:  # noqa: BLE001
            self._view.print_error(f"Error building repo map: {exc}")
            return
        if compact:
            self._view.print_info(f"# Repo Map\n{compact}")
        else:
            self._view.print_info("No source files found.")

    def _cmd_mcp(self, args: str) -> None:
        """MCP server sub-command router: install/remove/list."""
        parts = args.strip().split(None, 1)
        sub = parts[0] if parts else ""
        subargs = parts[1] if len(parts) > 1 else ""
        if sub == "install" and subargs:
            pkg = subargs.strip()
            short_name = pkg.split("/")[-1] if "/" in pkg else pkg
            config_path = Path.home() / ".llmcode" / "config.json"
            try:
                import json as _json
                config_data: dict = {}
                if config_path.exists():
                    config_data = _json.loads(config_path.read_text())
                # v2.5.3 — config.py reads ``mcpServers`` (camelCase, the
                # canonical Claude-Code-compatible key); a pre-v2.5.3
                # bug wrote ``mcp_servers`` (snake_case) here so installs
                # weren't loaded on next startup. Migrate any existing
                # snake-case entries forward, then write canonical key.
                if "mcp_servers" in config_data:
                    legacy = config_data.pop("mcp_servers")
                    config_data.setdefault("mcpServers", {}).update(legacy)
                mcp_block = config_data.setdefault("mcpServers", {})
                entry = {"command": "npx", "args": ["-y", pkg]}
                # v2.5.4 + v2.5.5 — split-schema handling.
                #
                # Detect the split schema documented in
                # ``runtime/config.MCPConfig``. New entries default to
                # ``always_on``. v2.5.5 also rescues any stranded
                # top-level entries left behind by pre-v2.5.4 installs
                # (they wrote new servers at the top level under split
                # schema, where the loader silently dropped them).
                # Promote them into ``always_on`` so the on-disk config
                # matches the schema the loader actually reads.
                if "always_on" in mcp_block or "on_demand" in mcp_block:
                    always_dict = mcp_block.setdefault("always_on", {})
                    stranded_keys = [
                        k for k, v in list(mcp_block.items())
                        if k not in {"always_on", "on_demand"}
                        and isinstance(v, dict)
                    ]
                    for k in stranded_keys:
                        # Existing always_on entries win on key collision.
                        always_dict.setdefault(k, mcp_block.pop(k))
                    always_dict[short_name] = entry
                else:
                    mcp_block[short_name] = entry
                config_path.parent.mkdir(parents=True, exist_ok=True)
                config_path.write_text(
                    _json.dumps(config_data, indent=2) + "\n"
                )
                if self._state.config is not None:
                    current = dict(self._state.config.mcp_servers or {})
                    current[short_name] = {
                        "command": "npx", "args": ["-y", pkg],
                    }
                    self._state.config = dataclasses.replace(
                        self._state.config, mcp_servers=current,
                    )
                self._view.print_info(
                    f"Added {short_name} to config. "
                    "Restart llmcode to start the server."
                )
            except Exception as exc:  # noqa: BLE001
                self._view.print_error(f"Install failed: {exc}")
            return
        if sub == "remove" and subargs:
            name = subargs.strip()
            config_path = Path.home() / ".llmcode" / "config.json"
            try:
                import json as _json
                if not config_path.exists():
                    self._view.print_info("No config file found.")
                    return
                config_data = _json.loads(config_path.read_text())
                # v2.5.3 — migrate snake-case key forward (see install
                # comment above) so /mcp remove works on configs written
                # by older llmcode versions.
                if "mcp_servers" in config_data:
                    legacy = config_data.pop("mcp_servers")
                    config_data.setdefault("mcpServers", {}).update(legacy)
                mcp_block = config_data.get("mcpServers", {})
                # v2.5.4 — search both the flat top level and the
                # split-schema sub-dicts so /mcp remove finds the server
                # regardless of where /mcp install (across versions)
                # placed it.
                target: dict | None = None
                _RESERVED = {"always_on", "on_demand"}
                if name in mcp_block and name not in _RESERVED:
                    target = mcp_block
                else:
                    for sub in ("always_on", "on_demand"):
                        sub_dict = mcp_block.get(sub)
                        if isinstance(sub_dict, dict) and name in sub_dict:
                            target = sub_dict
                            break
                if target is None:
                    self._view.print_info(
                        f"MCP server '{name}' not found in config."
                    )
                    return
                del target[name]
                config_path.write_text(
                    _json.dumps(config_data, indent=2) + "\n"
                )
                if self._state.config is not None:
                    current = dict(self._state.config.mcp_servers or {})
                    current.pop(name, None)
                    self._state.config = dataclasses.replace(
                        self._state.config, mcp_servers=current,
                    )
                self._view.print_info(f"Removed {name} from config.")
            except Exception as exc:  # noqa: BLE001
                self._view.print_error(f"Remove failed: {exc}")
            return
        # Default: list configured + known MCP servers.
        lines = ["Configured MCP servers:"]
        servers = {}
        if self._state.config and self._state.config.mcp_servers:
            servers = self._state.config.mcp_servers
        if servers:
            for name, cfg in servers.items():
                cmd = ""
                if isinstance(cfg, dict):
                    cmd = (
                        f"{cfg.get('command', '')} "
                        f"{' '.join(cfg.get('args', []))}".strip()
                    )
                lines.append(f"  {name}: {cmd or '(configured)'}")
        else:
            lines.append("  (none)")
        lines.append("")
        lines.append("Usage: /mcp install <pkg> | /mcp remove <name>")
        self._view.print_info("\n".join(lines))

    def _cmd_ide(self, args: str) -> None:
        """Show IDE bridge status (or connect hint)."""
        sub = args.strip().lower()
        bridge = self._state.ide_bridge
        if sub == "connect":
            self._view.print_info(
                "IDE bridge starts automatically when configured. "
                "Set ide.enabled=true in config."
            )
            return
        if bridge is None:
            self._view.print_info(
                "IDE integration is disabled. "
                "Set ide.enabled=true in config."
            )
            return
        try:
            if bridge.is_connected:
                ides = (
                    bridge._server.connected_ides
                    if bridge._server else []
                )
                names = ", ".join(ide.name for ide in ides) if ides else "unknown"
                self._view.print_info(f"IDE connected: {names}")
            else:
                port = bridge._config.port
                self._view.print_info(
                    f"IDE bridge listening on port {port}, no IDE connected."
                )
        except Exception as exc:  # noqa: BLE001
            self._view.print_error(f"IDE status error: {exc}")

    def _cmd_hida(self, args: str) -> None:
        """Show the last HIDA classification profile."""
        runtime = self._state.runtime
        if runtime is None or not hasattr(runtime, "_last_hida_profile"):
            self._view.print_info("HIDA: not initialized")
            return
        profile = runtime._last_hida_profile
        if profile is None:
            cfg = self._state.config
            hida_enabled = (
                getattr(cfg, "hida", None) is not None
                and cfg.hida.enabled
            )
            status = "enabled" if hida_enabled else "disabled"
            self._view.print_info(
                f"HIDA: {status}, no classification yet"
            )
            return
        try:
            from llm_code.runtime.hida import HidaEngine
            engine = HidaEngine()
            summary = engine.build_summary(profile)
            self._view.print_info(f"HIDA: {summary}")
        except Exception as exc:  # noqa: BLE001
            self._view.print_error(f"HIDA: {exc}")

    def _cmd_lsp(self, args: str) -> None:
        """LSP status. (v1.x was a stub; v2.0.0 adds real status if
        the manager is present.)"""
        mgr = self._state.lsp_manager
        if mgr is None:
            self._view.print_info("LSP: not started in this session.")
            return
        self._view.print_info(
            "LSP: manager initialized. Tools available: "
            "goto_definition, find_references, diagnostics, hover, "
            "document_symbol, workspace_symbol, implementation, "
            "call_hierarchy."
        )

    def _cmd_skill(self, args: str) -> None:
        """Skill sub-command router: install / enable / disable / remove / list."""
        parts = args.strip().split(None, 1)
        sub = parts[0] if parts else ""
        subargs = parts[1] if len(parts) > 1 else ""
        import tempfile
        import shutil as _shutil

        if sub == "install" and subargs:
            source = subargs.strip()
            if not self._is_valid_repo(source):
                self._view.print_error("Usage: /skill install owner/repo")
                return
            repo = source.replace("https://github.com/", "").rstrip("/")
            name = repo.split("/")[-1]
            dest = Path.home() / ".llmcode" / "skills" / name
            if dest.exists():
                _shutil.rmtree(dest)
            self._view.print_info(f"Cloning {repo}…")
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    result = subprocess.run(
                        [
                            "git", "clone", "--depth", "1",
                            f"https://github.com/{repo}.git", tmp,
                        ],
                        capture_output=True, text=True, timeout=30,
                        check=False,
                    )
                    if result.returncode != 0:
                        logger.warning(
                            "Skill clone failed for %s: %s",
                            repo, result.stderr[:200],
                        )
                        self._view.print_error(
                            "Clone failed. Check the repository URL."
                        )
                        return
                    skills_src = Path(tmp) / "skills"
                    if skills_src.is_dir():
                        _shutil.copytree(skills_src, dest)
                    else:
                        _shutil.copytree(tmp, dest)
                self._reload_skills()
                self._view.print_info(f"Installed {name}. Activated.")
            except Exception as exc:  # noqa: BLE001
                self._view.print_error(f"Install failed: {exc}")
            return
        if sub == "enable" and subargs:
            if not self._is_safe_name(subargs):
                self._view.print_error("Invalid skill name.")
                return
            marker = Path.home() / ".llmcode" / "skills" / subargs / ".disabled"
            marker.unlink(missing_ok=True)
            self._reload_skills()
            self._view.print_info(f"Enabled {subargs}")
            return
        if sub == "disable" and subargs:
            if not self._is_safe_name(subargs):
                self._view.print_error("Invalid skill name.")
                return
            marker = Path.home() / ".llmcode" / "skills" / subargs / ".disabled"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
            self._reload_skills()
            self._view.print_info(f"Disabled {subargs}")
            return
        if sub == "remove" and subargs:
            if not self._is_safe_name(subargs):
                self._view.print_error("Invalid skill name.")
                return
            d = Path.home() / ".llmcode" / "skills" / subargs
            if not d.is_dir():
                self._view.print_info(f"Not found: {subargs}")
                return
            _shutil.rmtree(d)
            self._reload_skills()
            self._view.print_info(f"Removed {subargs}")
            return
        # default: fall through to flat listing (interactive browser is
        # in _acmd_skill which dispatch_async prefers)
        self._list_skills_flat()

    async def _acmd_skill(self, args: str) -> None:
        """Async skill handler — preferred by dispatch_async.

        When args specify a sub-command (install/enable/disable/remove),
        delegates to the sync ``_cmd_skill``. When bare ``/skill`` is
        entered (no args), shows an interactive ``show_select`` dialog
        with ↑/↓ keyboard navigation.
        """
        parts = args.strip().split(None, 1)
        sub = parts[0] if parts else ""
        # Sub-commands go through the sync handler.
        if sub in ("install", "enable", "disable", "remove"):
            self._cmd_skill(args)
            return
        # Bare /skill → interactive browser.
        try:
            await self._interactive_skill_browser()
        except Exception as exc:  # noqa: BLE001
            logger.exception("/skill interactive browser failed")
            self._view.print_error(f"skill browser failed: {exc}")
            # Fallback to flat list so the user still sees something.
            self._list_skills_flat()

    def _reload_skills(self) -> None:
        """Rebuild ``state.skills`` from the four configured skill layers.

        Delegates to the same helper AppState.from_config uses, so
        whatever logic ships with skill loading stays in sync.
        """
        try:
            from llm_code.runtime.app_state import _load_skills_for_cwd
            self._state.skills = _load_skills_for_cwd(self._state.cwd)
        except Exception as exc:  # noqa: BLE001
            logger.warning("skill reload failed: %r", exc)

    def _activate_plugin_tools(self, plugin_dir: Path) -> None:
        """v16 M3 — feed a freshly-installed plugin through the executor.

        Reads ``.claude-plugin/plugin.json`` (if present), then calls
        :func:`marketplace.executor.load_plugin` to register every
        ``providesTools`` entry into the runtime's tool registry. A
        plugin without a manifest, without ``providesTools``, or with
        an unparsable manifest is silently treated as a no-op so the
        non-tool features (commands, skills, hooks) still work.
        """
        try:
            from llm_code.marketplace.executor import (
                PluginConflictError,
                PluginLoadError,
                load_plugin,
            )
            from llm_code.marketplace.plugin import PluginManifest
        except ImportError:
            return
        try:
            manifest = PluginManifest.from_path(plugin_dir)
        except FileNotFoundError:
            # Plugin without a Claude-Code-shaped manifest. Treat the
            # install as skill/asset only — no executor wiring needed.
            return
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning(
                "plugin %s manifest invalid: %r", plugin_dir.name, exc,
            )
            return

        tool_reg = getattr(self._state, "tool_reg", None)
        if tool_reg is None:
            return
        try:
            load_plugin(
                manifest,
                plugin_dir,
                tool_registry=tool_reg,
            )
        except PluginConflictError as exc:
            self._view.print_error(
                f"plugin tool clash for {plugin_dir.name}: {exc}"
            )
        except PluginLoadError as exc:
            self._view.print_error(
                f"plugin {plugin_dir.name} failed to register tools: {exc}"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "plugin %s executor wiring failed: %r",
                plugin_dir.name, exc,
            )

    async def _interactive_skill_browser(self) -> None:
        """M15: Interactive skill browser with ↑/↓ keyboard selection.

        Uses the coordinator's ``start_inline_select`` which renders
        the selection list directly in the main HSplit layout
        (replacing the input area temporarily). This avoids the
        Float size constraint entirely.
        """
        from llm_code.view.repl.components.inline_select import SelectionChoice

        choices: list[SelectionChoice] = []

        # Installed skills
        installed_names: set[str] = set()
        if self._state.skills is not None:
            all_skills = list(self._state.skills.auto_skills) + list(
                self._state.skills.command_skills
            )
            for s in all_skills:
                installed_names.add(s.name)
                mode = "auto" if getattr(s, "auto", False) else f"/{s.trigger}"
                tokens = len(s.content) // 4
                choices.append(SelectionChoice(
                    value=f"installed:{s.name}",
                    label=f"✓ {s.name}",
                    hint=f"{mode}, ~{tokens} tokens",
                ))

        # Marketplace (not yet installed)
        try:
            from llm_code.marketplace.builtin_registry import (
                get_all_known_plugins,
            )
            known = [
                p for p in get_all_known_plugins()
                if p["name"] not in installed_names
            ]
            for p in known:
                choices.append(SelectionChoice(
                    value=f"marketplace:{p['name']}:{p.get('repo', '')}",
                    label=f"  {p['name']}",
                    hint=p.get("desc", ""),
                ))
        except Exception:
            pass

        if not choices:
            self._view.print_info("No skills found (installed or marketplace).")
            return

        # Get the coordinator for inline select
        coordinator = getattr(self._view, "coordinator", None)
        if coordinator is None:
            self._list_skills_flat()
            return

        result = await coordinator.start_inline_select(
            prompt="Select a skill (↑/↓ navigate, Enter select, Esc cancel):",
            choices=choices,
        )
        if result is None:
            return

        parts = result.split(":", 2)
        kind = parts[0]
        name = parts[1] if len(parts) > 1 else ""

        if kind == "installed":
            # Second-level select for action
            action_choices = [
                SelectionChoice(value="remove", label=f"Remove {name}"),
                SelectionChoice(value="disable", label=f"Disable {name}"),
                SelectionChoice(value="cancel", label="Cancel"),
            ]
            action = await coordinator.start_inline_select(
                prompt=f"Action for {name}:",
                choices=action_choices,
            )
            if action == "remove":
                self._cmd_skill(f"remove {name}")
            elif action == "disable":
                self._cmd_skill(f"disable {name}")
        elif kind == "marketplace":
            repo = parts[2] if len(parts) > 2 else ""
            if repo:
                self._view.print_info(f"Installing {name} from {repo}…")
                self._cmd_skill(f"install {repo}")
            else:
                self._view.print_info(
                    f"No repo URL for {name}. Use: /skill install owner/repo"
                )

    def _list_skills_flat(self) -> None:
        """Fallback flat text list (no interactive dialog)."""
        installed_names: set[str] = set()
        lines: list[str] = ["Installed skills:"]
        if self._state.skills is not None:
            all_skills = list(self._state.skills.auto_skills) + list(
                self._state.skills.command_skills
            )
            if not all_skills:
                lines.append("  (none)")
            for s in all_skills:
                installed_names.add(s.name)
                tokens = len(s.content) // 4
                mode = "auto" if getattr(s, "auto", False) else f"/{s.trigger}"
                lines.append(f"  {s.name}  ({mode}, ~{tokens} tokens)")
        else:
            lines.append("  (skills subsystem not initialized)")
        try:
            from llm_code.marketplace.builtin_registry import (
                get_all_known_plugins,
            )
            known = [
                p for p in get_all_known_plugins()
                if p["name"] not in installed_names
            ]
            if known:
                lines.append("")
                lines.append("Available in marketplace:")
                for p in known:
                    desc = p.get("desc", "")
                    lines.append(f"  {p['name']} — {desc}")
        except Exception:
            pass
        lines.append("")
        lines.append(
            "Usage: /skill install owner/repo | enable <name> | "
            "disable <name> | remove <name>"
        )
        self._view.print_info("\n".join(lines))

    def _cmd_plugin(self, args: str) -> None:
        """Plugin sub-command router.

        v16 M3 — ``install`` routes through
        :meth:`PluginInstaller.install_from_github` so the security
        scan runs and any ``providesTools`` declared in the
        manifest are wired into the live runtime tool registry via
        :func:`marketplace.executor.load_plugin`.
        """
        parts = args.strip().split(None, 1)
        sub = parts[0] if parts else ""
        subargs = parts[1] if len(parts) > 1 else ""
        try:
            from llm_code.marketplace.installer import PluginInstaller
            installer = PluginInstaller(Path.home() / ".llmcode" / "plugins")
        except ImportError:
            self._view.print_error("Plugin system not available.")
            return

        if sub == "install" and subargs:
            source = subargs.strip()
            if not self._is_valid_repo(source):
                self._view.print_error("Usage: /plugin install owner/repo")
                return
            repo = source.replace("https://github.com/", "").rstrip("/")
            self._view.print_info(f"Cloning {repo} (with security scan)…")
            try:
                from llm_code.marketplace.installer import SecurityScanError
                # Schedule the async install on the running event loop
                # if there is one; otherwise run it eagerly. The same
                # pattern AgentTool uses for its own asyncio bridge.
                import asyncio as _asyncio
                import concurrent.futures as _cf

                async def _install_async() -> Path:
                    return await installer.install_from_github(repo, ref="main")

                try:
                    loop = _asyncio.get_running_loop()
                except RuntimeError:
                    loop = None
                if loop and loop.is_running():
                    with _cf.ThreadPoolExecutor() as pool:
                        dest_path = pool.submit(
                            _asyncio.run, _install_async(),
                        ).result()
                else:
                    dest_path = _asyncio.run(_install_async())
            except SecurityScanError as exc:
                logger.warning(
                    "plugin %s blocked by security scan: %s", repo, exc,
                )
                self._view.print_error(
                    f"Install blocked by security scan: {exc}"
                )
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Plugin clone failed for %s: %r", repo, exc,
                )
                self._view.print_error(f"Install failed: {exc}")
                return

            # Try to wire any provides_tools entries via the executor.
            # Failures here are non-fatal — the plugin is still
            # installed, just without dynamic tool registration.
            installed_name = dest_path.name
            self._activate_plugin_tools(dest_path)

            installer.enable(installed_name)
            self._reload_skills()
            self._view.print_info(
                f"Installed {installed_name}. Activated."
            )
            return
        if sub == "enable" and subargs:
            if not self._is_safe_name(subargs):
                self._view.print_error("Invalid plugin name.")
                return
            try:
                installer.enable(subargs)
                self._reload_skills()
                self._view.print_info(f"Enabled {subargs}")
            except Exception as exc:  # noqa: BLE001
                self._view.print_error(f"Enable failed: {exc}")
            return
        if sub == "disable" and subargs:
            if not self._is_safe_name(subargs):
                self._view.print_error("Invalid plugin name.")
                return
            try:
                installer.disable(subargs)
                self._reload_skills()
                self._view.print_info(f"Disabled {subargs}")
            except Exception as exc:  # noqa: BLE001
                self._view.print_error(f"Disable failed: {exc}")
            return
        if sub in ("remove", "uninstall") and subargs:
            if not self._is_safe_name(subargs):
                self._view.print_error("Invalid plugin name.")
                return
            try:
                installer.uninstall(subargs)
                self._reload_skills()
                self._view.print_info(f"Removed {subargs}")
            except Exception as exc:  # noqa: BLE001
                self._view.print_error(f"Remove failed: {exc}")
            return
        # default: list installed + known
        lines: list[str] = ["Installed plugins:"]
        installed_names: set[str] = set()
        try:
            installed = installer.list_installed()
            if not installed:
                lines.append("  (none)")
            for p in installed:
                installed_names.add(p.manifest.name)
                desc = getattr(p.manifest, "description", "")
                status = "enabled" if p.enabled else "disabled"
                lines.append(
                    f"  {p.manifest.name} v{p.manifest.version} "
                    f"[{status}] — {desc}"
                )
        except Exception:
            pass
        try:
            from llm_code.marketplace.builtin_registry import (
                get_all_known_plugins,
            )
            known = [
                p for p in get_all_known_plugins()
                if p["name"] not in installed_names
            ]
            if known:
                lines.append("")
                lines.append("Available in marketplace:")
                for p in known:
                    lines.append(
                        f"  {p['name']} — {p.get('desc', '')}"
                    )
        except Exception:
            pass
        lines.append("")
        lines.append(
            "Usage: /plugin install owner/repo | enable <name> | "
            "disable <name> | remove <name>"
        )
        self._view.print_info("\n".join(lines))

    def _cmd_auth(self, args: str) -> None:
        """v16 M6 — manage provider credentials.

        Subcommands:

        * ``list``                  — table of providers + status.
        * ``status``                — verbose dump (env-var override
          + stored credentials, both redacted).
        * ``login <provider>``      — invoke handler.login(); for
          OAuth, prints the device-code URL.
        * ``logout <provider>``     — clear stored credentials.

        Default (no args) shows ``list``.
        """
        from llm_code.runtime.auth import (
            AuthError,
            UnknownProviderError,
            get_handler,
            list_providers,
        )

        parts = args.strip().split(None, 1)
        sub = parts[0] if parts else ""
        subargs = parts[1] if len(parts) > 1 else ""

        if sub in ("", "list"):
            lines = ["Provider auth status:"]
            for name in list_providers():
                handler = get_handler(name)
                status = handler.status()
                tag = "logged-in" if status.logged_in else "not-logged-in"
                method = f" via {status.method}" if status.method else ""
                redacted = (
                    f" [{status.redacted_token}]"
                    if status.redacted_token else ""
                )
                note = f" — {status.note}" if status.note else ""
                lines.append(f"  {name:<14s} {tag}{method}{redacted}{note}")
            lines.append("")
            lines.append(
                "Usage: /auth login <provider> | logout <provider> | "
                "status | list"
            )
            self._view.print_info("\n".join(lines))
            return

        if sub == "status":
            # Same as ``list`` but verbose: include the help URL when
            # available so users know where to land for paid keys.
            lines = ["Provider auth detailed status:"]
            for name in list_providers():
                handler = get_handler(name)
                status = handler.status()
                tag = "logged-in" if status.logged_in else "not-logged-in"
                lines.append(f"  {name}: {tag}")
                if status.method:
                    lines.append(f"    method: {status.method}")
                if status.redacted_token:
                    lines.append(f"    token : {status.redacted_token}")
                env_var = getattr(handler, "env_var", "")
                if env_var:
                    lines.append(f"    env   : {env_var}")
                help_url = getattr(handler, "api_key_help_url", "")
                if help_url:
                    lines.append(f"    help  : {help_url}")
                if status.note:
                    lines.append(f"    note  : {status.note}")
            self._view.print_info("\n".join(lines))
            return

        if sub == "login" and subargs:
            try:
                handler = get_handler(subargs.strip())
            except UnknownProviderError as exc:
                self._view.print_error(str(exc))
                return
            try:
                result = handler.login()
            except AuthError as exc:
                self._view.print_error(f"Login failed: {exc}")
                return
            note = f" ({result.note})" if result.note else ""
            self._view.print_info(
                f"Logged in to {handler.display_name} via {result.method}.{note}"
            )
            return

        if sub == "logout" and subargs:
            try:
                handler = get_handler(subargs.strip())
            except UnknownProviderError as exc:
                self._view.print_error(str(exc))
                return
            handler.logout()
            self._view.print_info(
                f"Logged out of {handler.display_name}."
            )
            return

        self._view.print_error(
            "Usage: /auth login <provider> | logout <provider> | "
            "status | list"
        )

    def _cmd_voice(self, args: str) -> None:
        """Voice sub-command router: on / off / status.

        In v2.0.0 the REPL's ``PollingRecorderAdapter`` (M9.5) is the
        primary voice path via Ctrl+G. This command duplicates the
        same start/stop/status surface for users who prefer typing.
        """
        arg = args.strip().lower()
        cfg = getattr(self._state.config, "voice", None) if self._state.config else None
        if cfg is None or not cfg.enabled:
            self._view.print_info(
                "Voice not configured. Set voice.enabled=true in "
                "config.json and pick a backend: local, whisper, "
                "google, or anthropic."
            )
            return

        if arg == "on":
            if self._state.voice_active:
                self._view.print_info(
                    "Voice already recording. Run /voice off to stop."
                )
                return
            try:
                from llm_code.tools.voice import AudioRecorder, detect_backend
                backend = detect_backend()
                recorder = AudioRecorder(
                    backend=backend,
                    silence_seconds=float(
                        getattr(cfg, "silence_seconds", 2.0) or 0.0
                    ),
                    silence_threshold=int(
                        getattr(cfg, "silence_threshold", 3000) or 3000
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                self._view.print_error(
                    f"Voice recorder init failed: {exc}"
                )
                return
            if self._state.voice_stt is None:
                try:
                    from llm_code.tools.voice import create_stt_engine
                    self._state.voice_stt = create_stt_engine(cfg)
                except Exception as exc:  # noqa: BLE001
                    self._view.print_error(
                        f"Voice STT init failed: {exc}"
                    )
                    return
            try:
                recorder.start()
            except Exception as exc:  # noqa: BLE001
                self._view.print_error(
                    f"Voice recording failed to start: {exc}"
                )
                return
            self._state.voice_recorder = recorder
            self._state.voice_active = True
            self._view.print_info(
                "Recording — run /voice off to stop and transcribe."
            )
            return

        if arg == "off":
            recorder = self._state.voice_recorder
            stt = self._state.voice_stt
            if not self._state.voice_active or recorder is None:
                self._view.print_info("Voice is not recording.")
                return
            self._state.voice_active = False
            self._state.voice_recorder = None
            try:
                audio_bytes = recorder.stop()
            except Exception as exc:  # noqa: BLE001
                self._view.print_error(f"Voice stop failed: {exc}")
                return
            if not audio_bytes:
                self._view.print_info(
                    "No audio captured. Check microphone permission."
                )
                return
            self._view.print_info(
                f"Transcribing {len(audio_bytes) / (2 * 16000):.1f}s of audio…"
            )
            self._schedule_coroutine(
                self._transcribe_voice(stt, audio_bytes, cfg.language)
            )
            return

        if arg == "":
            if self._state.voice_active:
                self._view.print_info(
                    "Voice: recording. Run /voice off to stop."
                )
            else:
                self._view.print_info(
                    f"Voice: idle. Backend={cfg.backend}, "
                    f"Language={cfg.language}. "
                    "Usage: /voice on to start, /voice off to stop."
                )
            return

        self._view.print_warning(
            f"Unknown /voice subcommand: {arg}. "
            "Usage: /voice | /voice on | /voice off"
        )

    async def _transcribe_voice(
        self, stt_engine, audio_bytes: bytes, language: str,
    ) -> None:
        """Run STT off the event loop and report the transcript."""
        if stt_engine is None:
            self._view.print_error("STT engine not configured.")
            return
        import asyncio
        try:
            text = await asyncio.to_thread(
                stt_engine.transcribe, audio_bytes, language,
            )
        except Exception as exc:  # noqa: BLE001
            self._view.print_error(f"STT failed: {exc}")
            return
        text = (text or "").strip()
        if not text:
            self._view.print_info("STT returned an empty transcript.")
            return
        self._view.print_info(f"Transcribed: {text}")

    def _cmd_cron(self, args: str) -> None:
        """List, add, or delete scheduled cron tasks."""
        storage = self._state.cron_storage
        if storage is None:
            self._view.print_info("Cron not available.")
            return
        sub = args.strip() if args.strip() else "list"
        if sub == "list":
            tasks = storage.list_all()
            if not tasks:
                self._view.print_info("No scheduled tasks.")
                return
            lines = [f"Scheduled tasks ({len(tasks)}):"]
            for t in tasks:
                flags = []
                if t.recurring:
                    flags.append("recurring")
                if t.permanent:
                    flags.append("permanent")
                flag_str = f" [{', '.join(flags)}]" if flags else ""
                fired = (
                    f", last fired: {t.last_fired_at:%Y-%m-%d %H:%M}"
                    if t.last_fired_at else ""
                )
                lines.append(
                    f"  {t.id}  {t.cron}  \"{t.prompt}\"{flag_str}{fired}"
                )
            self._view.print_info("\n".join(lines))
            return
        if sub.startswith("delete "):
            task_id = sub.split(None, 1)[1].strip()
            removed = storage.remove(task_id)
            if removed:
                self._view.print_info(f"Deleted task {task_id}")
            else:
                self._view.print_info(f"Task '{task_id}' not found")
            return
        if sub == "add":
            self._view.print_info(
                "Use the cron_create tool to schedule a task:\n"
                "  cron: '0 9 * * *'  (5-field cron expression)\n"
                "  prompt: 'your prompt here'\n"
                "  recurring: true/false\n"
                "  permanent: true/false"
            )
            return
        self._view.print_info("Usage: /cron [list|add|delete <id>]")

    def _cmd_task(self, args: str) -> None:
        """Task lifecycle sub-command router."""
        parts = args.strip().split(None, 1)
        sub = parts[0] if parts else ""
        if sub in ("new", ""):
            self._view.print_info(
                "Use the task tools directly to create or manage tasks."
            )
            return
        if sub == "list":
            mgr = self._state.task_manager
            if mgr is None:
                self._view.print_info("Task manager not initialized.")
                return
            try:
                tasks = mgr.list_tasks(exclude_done=False)
            except Exception as exc:  # noqa: BLE001
                self._view.print_error(f"Error listing tasks: {exc}")
                return
            if not tasks:
                self._view.print_info("No tasks found.")
                return
            lines = ["Tasks:"]
            for t in tasks:
                lines.append(
                    f"  {t.id}  [{t.status.value:8s}]  {t.title}"
                )
            self._view.print_info("\n".join(lines))
            return
        if sub in ("verify", "close"):
            self._view.print_info("Use the task tools directly.")
            return
        self._view.print_info(
            "Usage: /task [new|verify <id>|close <id>|list]"
        )

    def _cmd_personas(self, args: str) -> None:
        """List built-in swarm personas."""
        try:
            from llm_code.swarm.personas import BUILTIN_PERSONAS
        except Exception as exc:  # noqa: BLE001
            self._view.print_error(f"personas import failed: {exc}")
            return
        lines = ["Available built-in personas:", ""]
        for name in sorted(BUILTIN_PERSONAS):
            persona = BUILTIN_PERSONAS[name]
            lines.append(f"  /{name:18s} — {persona.description}")
        self._view.print_info("\n".join(lines))

    def _cmd_orchestrate(self, args: str) -> None:
        """Run OrchestratorHook with inline LLM execution."""
        task = args.strip()
        if not task:
            self._view.print_info(
                "Usage: /orchestrate <task description>\n"
                "Routes the task to a persona by category and "
                "retries with fallback personas on failure."
            )
            return
        if self._state.runtime is None:
            self._view.print_error("Orchestrate: runtime not ready.")
            return
        self._schedule_coroutine(self._run_orchestrate(task))

    async def _run_orchestrate(self, task: str) -> None:
        try:
            from llm_code.runtime.orchestrate_executor import (
                make_inline_persona_executor, sync_wrap,
            )
            from llm_code.swarm.orchestrator_hook import (
                OrchestratorHook, categorize,
            )
            runtime = self._state.runtime
            executor = make_inline_persona_executor(runtime)
            hook = OrchestratorHook(executor=sync_wrap(executor))
            import asyncio
            result = await asyncio.to_thread(hook.orchestrate, task)
            category = categorize(task)
            success_attempt = next(
                (a for a in result.attempts if a.success), None
            )
            if success_attempt is not None:
                self._view.print_info(
                    f"[persona: {success_attempt.persona}]"
                )
                self._view.print_info(result.final_output)
                return
            lines = [f"Orchestrate failed (category={category}):", ""]
            for a in result.attempts:
                lines.append(
                    f"  attempt {a.attempt}: {a.persona} -> FAIL: {a.error}"
                )
            self._view.print_warning("\n".join(lines))
        except Exception as exc:  # noqa: BLE001
            self._view.print_error(f"Orchestrate failed: {exc}")

    def _cmd_swarm(self, args: str) -> None:
        """Swarm sub-command router (coordinate / status)."""
        parts = args.strip().split(None, 1)
        sub = parts[0] if parts else ""
        rest = parts[1].strip() if len(parts) > 1 else ""
        if sub == "coordinate":
            if not rest:
                self._view.print_info("Usage: /swarm coordinate <task>")
                return
            self._view.print_info(
                "Swarm coordination: use the swarm tools directly."
            )
            return
        if self._state.swarm_manager is None:
            self._view.print_info(
                "Swarm: not enabled. Set swarm.enabled=true in config."
            )
        else:
            self._view.print_info(
                "Swarm: active\nUsage: /swarm coordinate <task>"
            )

    def _cmd_vcr(self, args: str) -> None:
        """VCR sub-command router: start / stop / list."""
        sub = args.strip().split(None, 1)[0] if args.strip() else ""
        if sub == "start":
            runtime = self._state.runtime
            existing = getattr(runtime, "_vcr_recorder", None) if runtime else None
            if existing is not None:
                self._view.print_info("VCR recording already active.")
                return
            try:
                import uuid
                from llm_code.runtime.vcr import VCRRecorder
                recordings_dir = Path.home() / ".llmcode" / "recordings"
                recordings_dir.mkdir(parents=True, exist_ok=True)
                session_id = uuid.uuid4().hex[:8]
                path = recordings_dir / f"{session_id}.jsonl"
                recorder = VCRRecorder(path)
                if runtime is not None:
                    runtime._vcr_recorder = recorder
                self._view.print_info(
                    f"VCR recording started: {path.name}"
                )
            except Exception as exc:  # noqa: BLE001
                self._view.print_error(f"VCR start failed: {exc}")
            return
        if sub == "stop":
            runtime = self._state.runtime
            recorder = getattr(runtime, "_vcr_recorder", None) if runtime else None
            if recorder is None:
                self._view.print_info("No active VCR recording.")
                return
            try:
                recorder.close()
            except Exception:
                pass
            if runtime is not None:
                runtime._vcr_recorder = None
            self._view.print_info("VCR recording stopped.")
            return
        if sub == "list":
            recordings_dir = Path.home() / ".llmcode" / "recordings"
            if not recordings_dir.is_dir():
                self._view.print_info("No recordings found.")
                return
            files = sorted(recordings_dir.glob("*.jsonl"))
            if not files:
                self._view.print_info("No recordings found.")
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
                self._view.print_info("\n".join(lines))
            except Exception as exc:  # noqa: BLE001
                self._view.print_error(f"VCR list failed: {exc}")
            return
        runtime = self._state.runtime
        active = (
            "active"
            if runtime is not None
            and getattr(runtime, "_vcr_recorder", None) is not None
            else "inactive"
        )
        self._view.print_info(f"VCR: {active}\nUsage: /vcr start|stop|list")

    # ── Batch D: remaining (copy / image / vim) ──────────────────

    def _cmd_copy(self, args: str) -> None:
        """Copy the last assistant response to the system clipboard.

        v1.x walked the ChatScrollView looking for the last
        ``AssistantText`` widget. v2.0.0 doesn't retain history in
        a queryable widget — the REPL commits streamed messages into
        terminal scrollback where they're natively selectable. The
        nearest equivalent is the last assistant message on
        ``runtime.session``.
        """
        runtime = self._state.runtime
        if runtime is None or not getattr(runtime, "session", None):
            self._view.print_info("No response to copy.")
            return
        for msg in reversed(runtime.session.messages):
            if msg.role != "assistant":
                continue
            text_parts = []
            for block in msg.content:
                if hasattr(block, "text") and getattr(block, "text", ""):
                    text_parts.append(block.text)
            text = "\n".join(text_parts).strip()
            if not text:
                continue
            try:
                import pyperclip
                pyperclip.copy(text)
                self._view.print_info("Copied to clipboard.")
            except Exception as exc:  # noqa: BLE001
                self._view.print_warning(
                    f"Clipboard copy failed ({exc}). "
                    "Install pyperclip for clipboard support. "
                    "The last response is still visible in the terminal."
                )
            return
        self._view.print_info("No response to copy.")

    def _cmd_image(self, args: str) -> None:
        """Attach an image to the next turn.

        v1.x inserted an image marker into the Textual InputBar. In
        v2.0.0 we append the loaded image into
        ``state.pending_images`` — the renderer forwards it to the
        runtime on the next ``run_turn``. No widget coupling needed.
        """
        if not args:
            self._view.print_info("Usage: /image <path>")
            return
        try:
            from llm_code.cli.image import load_image_from_path
            img_path = Path(args).expanduser().resolve()
            img = load_image_from_path(str(img_path))
        except FileNotFoundError:
            self._view.print_error(f"Image not found: {args}")
            return
        except Exception as exc:  # noqa: BLE001
            self._view.print_error(f"Image load failed: {exc}")
            return
        self._state.pending_images.append(img)
        self._view.print_info(
            f"Image queued: {img_path.name} "
            f"(will attach to next turn)"
        )

    def _cmd_vim(self, args: str) -> None:
        """v16 M4 — toggle prompt_toolkit's vim editing mode at runtime.

        ``/vim``           → show current state
        ``/vim on``        → switch to ``EditingMode.VI``
        ``/vim off``       → switch to ``EditingMode.EMACS``
        ``/vim toggle``    → flip current state
        """
        cfg = self._state.config
        current = bool(getattr(cfg, "vim_mode", False)) if cfg is not None else False

        arg = args.strip().lower()
        if not arg:
            state = "on" if current else "off"
            self._view.print_info(
                f"Vim mode is {state}. Usage: /vim on | off | toggle"
            )
            return

        if arg == "toggle":
            target = not current
        elif arg in ("on", "true", "1", "yes"):
            target = True
        elif arg in ("off", "false", "0", "no"):
            target = False
        else:
            self._view.print_error(
                "Usage: /vim on | off | toggle"
            )
            return

        # Apply to the live prompt_toolkit Application via the coordinator.
        coordinator = getattr(self._view, "coordinator", None)
        applied = False
        if coordinator is not None:
            try:
                from prompt_toolkit.enums import EditingMode

                app = getattr(coordinator, "_app", None)
                if app is not None:
                    app.editing_mode = (
                        EditingMode.VI if target else EditingMode.EMACS
                    )
                    if getattr(app, "is_running", False):
                        app.invalidate()
                    applied = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("vim mode toggle failed: %r", exc)

        # Persist to config regardless of whether the live app picked it
        # up — next session start will honour the setting.
        if cfg is not None:
            try:
                import dataclasses as _dc
                if _dc.is_dataclass(cfg) and getattr(
                    type(cfg), "__dataclass_params__", None,
                ) and type(cfg).__dataclass_params__.frozen:
                    self._state.config = _dc.replace(cfg, vim_mode=target)
                else:
                    setattr(cfg, "vim_mode", target)
            except Exception:  # noqa: BLE001
                pass

        logger.info("vim_mode_set state=%s applied=%s", target, applied)
        suffix = "" if applied else " (will take effect next session)"
        self._view.print_info(
            f"Vim mode {'on' if target else 'off'}.{suffix}"
        )

    # ── v16 M10 — per-call MCP approval + transcript pager ───────────

    def _cmd_approve(self, args: str) -> None:
        """v16 M10 — manage MCP per-call approvals.

        ``/approve``                    → list current approvals
        ``/approve <tool>``             → grant one-shot approval for ``<tool>``
        ``/approve <tool> --session``   → grant session-wide approval

        Approvals are tracked in
        :class:`llm_code.runtime.permissions.MCPCallApproval`. The
        ``--session`` form skips per-call args matching for the rest
        of the session; the bare form approves the next call only.
        """
        from llm_code.runtime.permissions import MCPCallApproval

        runtime = getattr(self._state, "runtime", None)
        if runtime is None:
            self._view.print_warning("/approve: runtime not initialised")
            return
        approval: MCPCallApproval | None = getattr(runtime, "mcp_call_approval", None)
        if approval is None:
            approval = MCPCallApproval()
            try:
                setattr(runtime, "mcp_call_approval", approval)
            except Exception:  # noqa: BLE001 — defensive
                self._view.print_warning(
                    "/approve: runtime does not accept attributes"
                )
                return

        text = args.strip()
        if not text:
            tools = approval.list_tool_grants()
            calls = approval.list_grants()
            if not tools and not calls:
                self._view.print_info("No active approvals.")
                return
            for tool in tools:
                self._view.print_info(f"  session-wide: {tool}")
            for grant in calls:
                self._view.print_info(
                    f"  per-call ({grant.scope}): {grant.tool_name} "
                    f"args={grant.args_hash[:8]}"
                )
            return

        # Parse "<tool> [--session]"
        parts = text.split()
        tool_name = parts[0]
        session_flag = any(p in {"--session", "-s"} for p in parts[1:])

        if session_flag:
            approval.approve_tool(tool_name)
            self._view.print_info(
                f"Approved {tool_name} session-wide (use /approve to list)."
            )
        else:
            # One-shot: pre-approve "any args" — represent as session
            # tool grant with scope=once. Args-specific grants are
            # produced by the runtime's prompt path, not the slash
            # command (the slash command can't see future args).
            approval.approve_tool(tool_name)
            # Mark as one-shot by also tracking via the call grant
            # registry so the next runtime call can consume it.
            approval.approve_call(tool_name, args={}, scope="once")
            self._view.print_info(
                f"Approved {tool_name} for the next call. "
                f"Use /approve {tool_name} --session for the whole session."
            )

        logger.info(
            "mcp_call_approval_granted tool=%s scope=%s",
            tool_name,
            "session" if session_flag else "once",
        )

    def _cmd_transcript(self, args: str) -> None:
        """v16 M10 — open the transcript pager over the SQLite state DB.

        ``/transcript``        → render the last 50 turns
        ``/transcript <N>``    → render the last N turns
        ``/transcript /needle``→ open with a search prefilled
        """
        from llm_code.runtime.state_db import get_state_db
        from llm_code.view.repl.components.transcript_pager import (
            TranscriptPager,
        )

        session = getattr(self._state, "session", None)
        if session is None or not getattr(session, "id", None):
            self._view.print_warning("/transcript: no active session")
            return

        max_turns = 50
        prefix_search: str | None = None
        text = args.strip()
        if text:
            if text.startswith("/"):
                prefix_search = text[1:]
            else:
                try:
                    max_turns = max(1, int(text))
                except ValueError:
                    self._view.print_warning(
                        f"/transcript: bad arg {text!r}; expected count or /needle"
                    )
                    return

        try:
            db = get_state_db()
        except Exception as exc:  # noqa: BLE001 — defensive
            self._view.print_error(f"/transcript: state_db unavailable: {exc}")
            return

        pager = TranscriptPager(
            state_db=db,
            session_id=session.id,
            max_turns=max_turns,
        )
        pager.open()
        if prefix_search:
            pager.begin_search()
            for ch in prefix_search:
                pager.update_search_buffer(ch)
            pager.commit_search()

        # /transcript renders the current viewport inline through the
        # existing print surface so the feature ships end-to-end
        # without introducing a new modal floating-overlay
        # infrastructure. The pager model itself is framework-
        # agnostic — bindings on a Float overlay would call the same
        # navigation methods.
        for line in pager.current_view():
            prefix = "* " if line.is_match else "  "
            self._view.print_info(f"{prefix}{line.text}")
        self._view.print_info(pager.status_line())
        pager.close()


__all__ = ["CommandDispatcher"]
