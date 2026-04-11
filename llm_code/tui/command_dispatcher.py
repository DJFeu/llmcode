# llm_code/tui/command_dispatcher.py
"""CommandDispatcher — all 51 slash-command handlers extracted from app.py."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from llm_code.logging import get_logger

if TYPE_CHECKING:
    from llm_code.tui.app import LLMCodeTUI

logger = get_logger(__name__)


def _render_session_markdown(session: Any) -> str:
    """Render a ``Session`` to a human-readable Markdown document.

    Walks ``session.messages`` in order and dispatches each content
    block type to a stable rendering. Thinking blocks are wrapped in a
    collapsible ``<details>`` so the export stays readable on GitHub
    but power users can still inspect the reasoning. Tool input/output
    are emitted as fenced code blocks. Image blocks are represented by
    a placeholder that names the media type rather than dumping the
    base64 payload, which would make the file unusable.

    Used by ``CommandDispatcher._cmd_export``. Kept as a module-level
    helper (not a method) so tests can exercise it without spinning up
    a whole TUI.
    """
    from datetime import datetime

    from llm_code.api.types import (
        ImageBlock,
        ServerToolResultBlock,
        ServerToolUseBlock,
        TextBlock,
        ThinkingBlock,
        ToolResultBlock,
        ToolUseBlock,
    )

    lines: list[str] = []
    title = session.name or f"Session {session.id}"
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"- **Session ID:** `{session.id}`")
    lines.append(f"- **Project:** `{session.project_path}`")
    lines.append(f"- **Created:** {session.created_at}")
    lines.append(f"- **Updated:** {session.updated_at}")
    lines.append(f"- **Messages:** {len(session.messages)}")
    lines.append(f"- **Exported at:** {datetime.now().isoformat(timespec='seconds')}")
    if session.tags:
        lines.append(f"- **Tags:** {', '.join(session.tags)}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for idx, msg in enumerate(session.messages, start=1):
        heading = "User" if msg.role == "user" else "Assistant" if msg.role == "assistant" else msg.role.title()
        lines.append(f"## {idx}. {heading}")
        lines.append("")
        for block in msg.content:
            if isinstance(block, TextBlock):
                lines.append(block.text.rstrip())
                lines.append("")
            elif isinstance(block, ThinkingBlock):
                lines.append("<details><summary>💭 thinking</summary>")
                lines.append("")
                lines.append("```")
                lines.append(block.content.rstrip())
                lines.append("```")
                lines.append("")
                lines.append("</details>")
                lines.append("")
            elif isinstance(block, (ToolUseBlock, ServerToolUseBlock)):
                import json as _json
                try:
                    pretty = _json.dumps(block.input, ensure_ascii=False, indent=2)
                except (TypeError, ValueError):
                    pretty = repr(block.input)
                lines.append(f"**🔧 tool call:** `{block.name}` (id=`{block.id}`)")
                lines.append("")
                lines.append("```json")
                lines.append(pretty)
                lines.append("```")
                lines.append("")
            elif isinstance(block, (ToolResultBlock, ServerToolResultBlock)):
                is_err = getattr(block, "is_error", False)
                marker = "❌ tool error" if is_err else "✅ tool result"
                lines.append(f"**{marker}** (tool_use_id=`{block.tool_use_id}`)")
                lines.append("")
                lines.append("```")
                lines.append(str(block.content).rstrip())
                lines.append("```")
                lines.append("")
            elif isinstance(block, ImageBlock):
                lines.append(f"*[image · {block.media_type}]*")
                lines.append("")
            else:
                # Unknown block type — fail open, show repr so nothing is lost.
                lines.append(f"*[{type(block).__name__}]* `{block!r}`")
                lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


class CommandDispatcher:
    """Dispatches /slash commands to handler methods.

    Holds a back-reference to the app so handlers can read and mutate
    app state exactly as they did when they lived inside LLMCodeTUI.
    """

    def __init__(self, app: "LLMCodeTUI") -> None:
        self._app = app

    # ── public dispatch entry-point ──────────────────────────────────

    def dispatch(self, name: str, args: str) -> bool:
        """Call ``_cmd_{name}(args)`` if it exists.  Return *True* if handled."""
        handler = getattr(self, f"_cmd_{name}", None)
        if handler is not None:
            handler(args)
            return True
        return False

    # ── helper shortcuts ─────────────────────────────────────────────
    # These keep the moved methods readable without ``self._app.`` noise
    # for the most-frequently-used accessors.

    def _chat(self):
        from llm_code.tui.chat_view import ChatScrollView
        return self._app.query_one(ChatScrollView)

    def _status(self):
        from llm_code.tui.status_bar import StatusBar
        return self._app.query_one(StatusBar)

    # ── static helpers (moved from app) ──────────────────────────────

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

    @staticmethod
    def _escape_fts5(query: str) -> str:
        """Escape special FTS5 characters to prevent query syntax errors."""
        tokens = query.split()
        if not tokens:
            return query
        return " ".join(f'"{t}"' for t in tokens)

    # ── slash-command handlers ───────────────────────────────────────

    def _cmd_compact(self, args: str) -> None:
        """Manually compact the conversation, freeing context window space."""
        from llm_code.tui.chat_view import ChatScrollView, AssistantText
        from llm_code.tui.status_bar import StatusBar

        chat = self._app.query_one(ChatScrollView)
        if self._app._runtime is None:
            chat.add_entry(AssistantText("Compaction unavailable: runtime not initialized."))
            return
        try:
            from llm_code.runtime.compaction import compact_session

            before_msgs = len(self._app._runtime.session.messages)
            before_toks = self._app._runtime.session.estimated_tokens()
            keep = 4
            try:
                keep = int(args.strip()) if args.strip() else 4
            except ValueError:
                keep = 4
            self._app._runtime.session = compact_session(
                self._app._runtime.session, keep_recent=keep, summary="(manual /compact)",
            )
            after_msgs = len(self._app._runtime.session.messages)
            after_toks = self._app._runtime.session.estimated_tokens()
            chat.add_entry(AssistantText(
                f"✓ Compacted: {before_msgs} → {after_msgs} messages, "
                f"~{before_toks:,} → ~{after_toks:,} tokens. "
                "Older messages summarized."
            ))
            try:
                status = self._app.query_one(StatusBar)
                status.context_used = after_toks
                self._app._context_warned = False
            except Exception:
                pass
        except Exception as exc:
            chat.add_entry(AssistantText(f"Compaction failed: {exc}"))

    def _cmd_exit(self, args: str) -> None:
        self._app.run_worker(self._app._graceful_exit(), name="graceful_exit")

    _cmd_quit = None  # will be set after class body

    def _cmd_export(self, args: str) -> None:
        """Export the current conversation to a Markdown file.

        Usage:
            /export                 → write to ./llmcode-export-<id>-<date>.md
            /export <path>          → write to the given path

        The output is a stable Markdown rendering of every message in the
        live session: user turns, assistant text/thinking, and tool
        use/result blocks. Image blocks are summarized by media type
        because base64 payloads are too large to be readable. The file is
        created with ``pathlib.Path.write_text`` in UTF-8.
        """
        from datetime import datetime
        from pathlib import Path

        from llm_code.tui.chat_view import AssistantText, ChatScrollView

        chat = self._app.query_one(ChatScrollView)

        runtime = self._app._runtime
        if runtime is None or not getattr(runtime, "session", None):
            chat.add_entry(AssistantText("Export unavailable: no active session."))
            return

        session = runtime.session
        messages = session.messages
        if not messages:
            chat.add_entry(AssistantText("Nothing to export — conversation is empty."))
            return

        target_arg = args.strip()
        if target_arg:
            target = Path(target_arg).expanduser()
            if not target.is_absolute():
                target = Path(self._app._cwd) / target
        else:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            default_name = f"llmcode-export-{session.id}-{stamp}.md"
            target = Path(self._app._cwd) / default_name

        try:
            markdown = _render_session_markdown(session)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(markdown, encoding="utf-8")
        except Exception as exc:
            chat.add_entry(AssistantText(f"Export failed: {exc}"))
            return

        chat.add_entry(AssistantText(
            f"✓ Exported {len(messages)} messages → {target}"
        ))

    def _cmd_help(self, args: str) -> None:
        """Open the interactive help modal.

        The modal has three tabs (general / commands / custom-commands).
        The two list tabs are rendered with Textual's built-in ``OptionList``
        instead of a hand-rolled ``Static + cursor`` so keyboard navigation,
        scrollbars, page-up/down, and focus highlight all work natively —
        the previous implementation tracked the cursor inside a single
        ``Static`` RichText, which meant ``VerticalScroll`` had no idea
        where the cursor was and never scrolled past the first viewport.
        """
        from rich.text import Text as RichText
        from textual.containers import Container
        from textual.screen import ModalScreen
        from textual.widgets import OptionList, Static
        from textual.widgets.option_list import Option

        from llm_code.cli.commands import COMMAND_REGISTRY
        from llm_code.tui.input_bar import InputBar

        skills = self._app._skills
        app_ref = self._app

        _COMMANDS: list[tuple[str, str]] = [
            (f"/{c.name}", c.description)
            for c in COMMAND_REGISTRY
            if c.name not in ("quit",)  # skip duplicate of /exit
        ]

        _custom_cmds: list[tuple[str, str]] = []
        if skills:
            for s in sorted(
                list(skills.auto_skills) + list(skills.command_skills),
                key=lambda x: x.name,
            ):
                trigger = f"/{s.trigger}" if s.trigger else f"(auto: {s.name})"
                desc = s.description if hasattr(s, "description") and s.description else s.name
                source = "user" if not getattr(s, "plugin", None) else f"({s.plugin})"
                _custom_cmds.append((trigger, f"{desc} {source}"))

        def _format_option(cmd: str, desc: str) -> RichText:
            text = RichText()
            text.append(cmd, style="bold white")
            text.append("\n  ")
            text.append(desc, style="dim")
            return text

        class HelpScreen(ModalScreen):
            DEFAULT_CSS = """
            HelpScreen { align: center middle; }
            #help-box {
                width: 90%;
                height: 85%;
                background: $surface;
                border: round $accent;
                padding: 1 2;
            }
            #help-tabs { height: 2; }
            #help-panes { height: 1fr; }
            #help-general { height: auto; padding: 0 1; }
            #help-commands, #help-custom {
                height: 1fr;
                background: $surface;
                border: none;
            }
            #help-commands:focus, #help-custom:focus {
                border: none;
            }
            #help-footer {
                dock: bottom;
                height: 1;
                color: $text-muted;
                text-align: center;
            }
            """

            BINDINGS = [("escape", "close", "Close")]

            def __init__(self) -> None:
                super().__init__()
                self._tab = 0
                self._tab_names = ["general", "commands", "custom-commands"]

            def compose(self):
                with Container(id="help-box"):
                    yield Static("", id="help-tabs")
                    with Container(id="help-panes"):
                        yield Static("", id="help-general")
                        # Pre-populate OptionLists so Textual knows their
                        # true content height for correct scroll math.
                        yield OptionList(
                            *[Option(_format_option(c, d), id=c) for c, d in _COMMANDS],
                            id="help-commands",
                        )
                        if _custom_cmds:
                            yield OptionList(
                                *[Option(_format_option(c, d), id=c) for c, d in _custom_cmds],
                                id="help-custom",
                            )
                        else:
                            placeholder = RichText()
                            placeholder.append(
                                "No custom commands installed.\n  ",
                                style="dim",
                            )
                            placeholder.append(
                                "Use /skill or /plugin to browse and install.",
                                style="dim",
                            )
                            yield OptionList(
                                Option(placeholder, id=None, disabled=True),
                                id="help-custom",
                            )
                yield Static(
                    "← → tabs · ↑↓ navigate · Enter execute · Esc close",
                    id="help-footer",
                )

            def on_mount(self) -> None:
                # Static general content — computed once, never changes.
                self.query_one("#help-general", Static).update(self._build_general())
                self._show_tab(0)

            def action_close(self) -> None:
                self.dismiss()

            def on_key(self, event) -> None:
                # Only intercept tab switching. up/down/page up/page down/
                # enter are all handled natively by the focused OptionList,
                # which is why scrolling and the scrollbar actually work now.
                if event.key == "left":
                    self._show_tab(max(0, self._tab - 1))
                    event.prevent_default()
                    event.stop()
                elif event.key == "right":
                    self._show_tab(min(2, self._tab + 1))
                    event.prevent_default()
                    event.stop()

            def _show_tab(self, idx: int) -> None:
                self._tab = idx
                self.query_one("#help-tabs", Static).update(self._render_header())
                # Show the active pane, hide the others.
                self.query_one("#help-general").display = idx == 0
                self.query_one("#help-commands").display = idx == 1
                self.query_one("#help-custom").display = idx == 2
                # Move keyboard focus to the active list so up/down work
                # immediately after a tab switch.
                if idx == 1:
                    self.query_one("#help-commands", OptionList).focus()
                elif idx == 2:
                    self.query_one("#help-custom", OptionList).focus()

            def on_option_list_option_selected(self, event) -> None:
                """Enter on a command option → close modal + dispatch it."""
                opt_id = event.option.id
                if not opt_id:
                    return
                self.dismiss()
                try:
                    app_ref.query_one(InputBar).value = ""
                except Exception:
                    pass
                app_ref._handle_slash_command(opt_id)

            def _render_header(self) -> RichText:
                text = RichText()
                text.append("llm-code", style="bold cyan")
                text.append("  ", style="dim")
                for i, name in enumerate(self._tab_names):
                    if i == self._tab:
                        text.append(f" {name} ", style="bold white on #3a3a5a")
                    else:
                        text.append(f"  {name}  ", style="dim")
                return text

            def _build_general(self) -> RichText:
                text = RichText()
                text.append(
                    "llm-code understands your codebase, makes edits with your "
                    "permission, and executes commands — right from your terminal.\n\n",
                    style="white",
                )
                text.append("Shortcuts\n", style="bold white")
                shortcuts = [
                    ("! for bash mode", "double tap esc to clear", "Ctrl+D to quit"),
                    ("/ for commands", "Shift+Enter for multiline", "Ctrl+I to paste images"),
                    ("/skill browse skills", "Page Up/Down to scroll", "/vim toggle vim"),
                    ("/plugin browse plugins", "Tab to autocomplete", "/model switch model"),
                    ("/mcp MCP servers", "Ctrl+O verbose output", "/undo revert changes"),
                ]
                for row in shortcuts:
                    for i, col in enumerate(row):
                        text.append(f"{col:<32s}", style="white" if i == 0 else "dim")
                    text.append("\n")
                return text

        self._app.push_screen(HelpScreen())

    def _cmd_copy(self, args: str) -> None:
        """Copy last assistant response to system clipboard."""
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)
        # Walk children in reverse to find last AssistantText
        for child in reversed(list(chat.children)):
            if isinstance(child, AssistantText):
                text = child._text
                if text:
                    self._app.copy_to_clipboard(text)
                    chat.add_entry(AssistantText("Copied to clipboard."))
                    return
        chat.add_entry(AssistantText("No response to copy."))

    def _cmd_clear(self, args: str) -> None:
        from llm_code.tui.chat_view import ChatScrollView
        self._app.query_one(ChatScrollView).remove_children()

    def _cmd_update(self, args: str) -> None:
        """Check for updates and optionally upgrade in-place."""
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)

        async def _do_update() -> None:
            from llm_code.cli.updater import check_update, run_upgrade
            chat.add_entry(AssistantText("Checking for updates..."))
            info = await check_update()
            if info is None:
                chat.add_entry(AssistantText("Already on the latest version."))
                return
            current, latest = info
            chat.add_entry(AssistantText(
                f"Update available: {current} → {latest}\n"
                f"Running: pip install --upgrade llmcode-cli"
            ))
            ok, output = await run_upgrade()
            if ok:
                chat.add_entry(AssistantText(
                    f"✓ Updated to {latest}. Restart llmcode to use the new version."
                ))
            else:
                chat.add_entry(AssistantText(f"✗ Update failed:\n{output}"))

        self._app.run_worker(_do_update(), name="update")

    def _cmd_theme(self, args: str) -> None:
        """Switch TUI color theme."""
        from llm_code.tui.theme import apply_theme
        from llm_code.tui.themes import list_themes
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)
        name = args.strip()

        if not name:
            available = list_themes()
            chat.add_entry(AssistantText(
                f"Available themes: {', '.join(available)}\n"
                f"Usage: /theme <name>"
            ))
            return

        theme = apply_theme(name)
        chat.add_entry(AssistantText(f"Theme switched to: {theme.display_name}"))

    def _cmd_model(self, args: str) -> None:
        from llm_code.tui.chat_view import ChatScrollView, AssistantText
        from llm_code.tui.header_bar import HeaderBar

        chat = self._app.query_one(ChatScrollView)
        if args.strip() == "route":
            self._show_model_routes()
            return
        if args:
            import dataclasses
            self._app._config = dataclasses.replace(self._app._config, model=args)
            self._app._init_runtime()
            self._app.query_one(HeaderBar).model = args
            chat.add_entry(AssistantText(f"Model switched to: {args}"))
            chat.add_entry(AssistantText(self._format_profile_info(args)))
        else:
            model = self._app._config.model if self._app._config else "(not set)"
            chat.add_entry(AssistantText(f"Current model: {model}"))
            if model and model != "(not set)":
                chat.add_entry(AssistantText(self._format_profile_info(model)))

    def _format_profile_info(self, model: str) -> str:
        """Format model profile as a compact info string."""
        from llm_code.runtime.model_profile import get_profile
        p = get_profile(model)
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
        parts.append(f"  Provider: {p.provider_type}  |  Context: {p.context_window:,}  |  Max output: {p.max_output_tokens:,}")
        if p.thinking_extra_body_format != "chat_template_kwargs":
            parts.append(f"  Thinking format: {p.thinking_extra_body_format}")
        if p.price_input > 0 or p.price_output > 0:
            parts.append(f"  Pricing: ${p.price_input:.2f}/${p.price_output:.2f} per 1M tokens")
        return "\n".join(parts)

    def _show_model_routes(self) -> None:
        """Display configured model routing table."""
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)
        routes: list[str] = []
        cfg = self._app._config
        if hasattr(cfg, "model") and cfg.model:
            routes.append(f"  {'default':<12s}  {cfg.model}")
        if hasattr(cfg, "model_routing") and cfg.model_routing:
            mr = cfg.model_routing
            for attr in ("sub_agent", "compaction", "fallback"):
                model = getattr(mr, attr, None)
                if model:
                    routes.append(f"  {attr:<12s}  {model}")
        if routes:
            chat.add_entry(AssistantText("Model routing:\n" + "\n".join(routes)))
        else:
            chat.add_entry(AssistantText("No model routing configured"))

    def _cmd_cost(self, args: str) -> None:
        from llm_code.tui.chat_view import ChatScrollView, AssistantText
        cost = self._app._cost_tracker.format_cost() if self._app._cost_tracker else "No cost data"
        self._app.query_one(ChatScrollView).add_entry(AssistantText(cost))

    def _cmd_cache(self, args: str) -> None:
        """Manage persistent caches (server capabilities + skill router).

        Sub-commands:
            /cache list   — show cached entries
            /cache clear  — wipe all caches
            /cache probe  — clear server-capabilities cache and re-probe
                            native tool support on the next turn
        """
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)
        sub = args.strip().lower().split()[0] if args.strip() else "list"

        if sub == "list":
            lines: list[str] = ["**Persistent caches:**\n"]
            # Server capabilities
            try:
                from llm_code.runtime.server_capabilities import _CACHE_PATH as _sc_path
                if _sc_path.exists():
                    import json as _json
                    data = _json.loads(_sc_path.read_text(encoding="utf-8"))
                    lines.append(f"**server_capabilities** ({_sc_path}):")
                    for key, entry in data.items():
                        native = entry.get("native_tools", "?")
                        cached_at = entry.get("cached_at", "?")
                        lines.append(f"  `{key}` → native_tools={native} (cached {cached_at})")
                else:
                    lines.append("**server_capabilities**: (no cache file)")
            except Exception as exc:
                lines.append(f"**server_capabilities**: error reading: {exc}")
            # Skill router cache
            try:
                from llm_code.runtime.skill_router_cache import _CACHE_PATH as _src_path
                if _src_path.exists():
                    import json as _json2
                    data2 = _json2.loads(_src_path.read_text(encoding="utf-8"))
                    total_entries = sum(
                        len(bucket.get("entries", {}))
                        for bucket in data2.values()
                        if isinstance(bucket, dict)
                    )
                    lines.append(f"\n**skill_router_cache** ({_src_path}): {total_entries} entries across {len(data2)} skill set(s)")
                else:
                    lines.append("\n**skill_router_cache**: (no cache file)")
            except Exception as exc:
                lines.append(f"\n**skill_router_cache**: error reading: {exc}")
            chat.add_entry(AssistantText("\n".join(lines)))

        elif sub == "clear":
            cleared: list[str] = []
            try:
                from llm_code.runtime.server_capabilities import clear_native_tools_cache
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
            # Also reset the in-memory force_xml flag so the
            # next turn re-probes native tool support.
            if self._app._runtime and hasattr(self._app._runtime, "_force_xml_mode"):
                self._app._runtime._force_xml_mode = False
            # Clear the in-memory skill router cache
            if self._app._runtime and hasattr(self._app._runtime, "_skill_router"):
                self._app._runtime._skill_router._cache.clear()
            chat.add_entry(AssistantText(
                f"Cleared: {', '.join(cleared) or 'nothing'}. "
                f"In-memory caches reset. Next turn will re-probe "
                f"server capabilities and re-run skill routing."
            ))

        elif sub == "probe":
            # Clear only server capabilities, keep skill router cache
            try:
                from llm_code.runtime.server_capabilities import clear_native_tools_cache
                clear_native_tools_cache()
            except Exception:
                pass
            if self._app._runtime and hasattr(self._app._runtime, "_force_xml_mode"):
                self._app._runtime._force_xml_mode = False
            chat.add_entry(AssistantText(
                "Server capabilities cache cleared. Next turn will "
                "re-probe native tool support. If your vLLM was "
                "upgraded with --enable-auto-tool-choice, llm-code "
                "will discover and cache the new capability."
            ))

        else:
            chat.add_entry(AssistantText(
                "Usage: `/cache list` | `/cache clear` | `/cache probe`"
            ))

    def _cmd_profile(self, args: str) -> None:
        """Show per-model token/cost breakdown from the query profiler."""
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)
        profiler = getattr(self._app._runtime, "_query_profiler", None) if self._app._runtime else None
        if profiler is None:
            chat.add_entry(AssistantText("(profiler not initialized)"))
            return
        pricing = getattr(self._app._config, "pricing", None)
        chat.add_entry(AssistantText(profiler.format_breakdown(pricing)))

    def _cmd_gain(self, args: str) -> None:
        from llm_code.tui.chat_view import ChatScrollView, AssistantText
        from llm_code.tools.token_tracker import TokenTracker
        days = int(args) if args.strip().isdigit() else 30
        tracker = TokenTracker()
        report = tracker.format_report(days)
        tracker.close()
        self._app.query_one(ChatScrollView).add_entry(AssistantText(report))

    def _cmd_cd(self, args: str) -> None:
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)
        if args:
            new_path = Path(args).expanduser()
            if not new_path.is_absolute():
                new_path = self._app._cwd / new_path
            new_path = new_path.resolve()
            if new_path.is_dir():
                self._app._cwd = new_path
                os.chdir(new_path)
                chat.add_entry(AssistantText(f"Working directory: {new_path}"))
            else:
                chat.add_entry(AssistantText(f"Directory not found: {new_path}"))
        else:
            chat.add_entry(AssistantText(f"Current directory: {self._app._cwd}"))

    def _cmd_budget(self, args: str) -> None:
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)
        if args:
            try:
                self._app._budget = int(args)
                chat.add_entry(AssistantText(f"Token budget set: {self._app._budget:,}"))
            except ValueError:
                chat.add_entry(AssistantText("Usage: /budget <number>"))
        elif self._app._budget is not None:
            chat.add_entry(AssistantText(f"Current token budget: {self._app._budget:,}"))
        else:
            chat.add_entry(AssistantText("No budget set."))

    def _cmd_undo(self, args: str) -> None:
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)
        if not self._app._checkpoint_mgr:
            chat.add_entry(AssistantText("Not in a git repository — undo not available."))
            return
        if args.strip() == "list":
            cps = self._app._checkpoint_mgr.list_checkpoints()
            if cps:
                lines = [f"  {cp.id}  {cp.tool_name}  {cp.timestamp[:19]}" for cp in cps]
                chat.add_entry(AssistantText("\n".join(lines)))
            else:
                chat.add_entry(AssistantText("No checkpoints."))
        elif self._app._checkpoint_mgr.can_undo():
            steps = 1
            if args.strip().isdigit():
                steps = int(args.strip())
            cp = self._app._checkpoint_mgr.undo(steps)
            if cp:
                label = f"Undone {steps} step(s)" if steps > 1 else "Undone"
                chat.add_entry(AssistantText(f"{label}: {cp.tool_name} ({cp.tool_args_summary[:50]})"))
        else:
            chat.add_entry(AssistantText("Nothing to undo."))

    def _cmd_diff(self, args: str) -> None:
        """Show diff since last checkpoint."""
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)
        if not self._app._checkpoint_mgr or not self._app._checkpoint_mgr.can_undo():
            chat.add_entry(AssistantText("No checkpoints available."))
            return
        last_cp = self._app._checkpoint_mgr.list_checkpoints()[-1]
        result = subprocess.run(
            ["git", "diff", last_cp.git_sha, "HEAD"],
            capture_output=True, text=True, cwd=self._app._cwd,
        )
        if result.stdout.strip():
            chat.add_entry(AssistantText(f"```diff\n{result.stdout}\n```"))
        else:
            chat.add_entry(AssistantText("No changes since last checkpoint."))

    def _cmd_init(self, args: str) -> None:
        """Run an LLM-driven analysis of the repo to generate AGENTS.md."""
        from pathlib import Path as _Path
        from llm_code.tui.chat_view import ChatScrollView, AssistantText
        from llm_code.tui.input_bar import InputBar

        chat = self._app.query_one(ChatScrollView)
        template_path = _Path(__file__).parent.parent / "cli" / "templates" / "init.md"
        if not template_path.is_file():
            chat.add_entry(AssistantText(f"Init template not found: {template_path}"))
            return
        try:
            template = template_path.read_text(encoding="utf-8")
        except OSError as exc:
            chat.add_entry(AssistantText(f"Failed to read init template: {exc}"))
            return
        prompt = template.replace("$ARGUMENTS", args.strip() or "(none)")
        chat.add_entry(AssistantText("Analyzing repo and generating AGENTS.md..."))
        images = list(self._app._pending_images)
        self._app._pending_images.clear()
        self._app.query_one(InputBar).pending_image_count = 0
        self._app.run_worker(self._app._run_turn(prompt, images=images), name="run_turn")

    def _cmd_index(self, args: str) -> None:
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)
        if args.strip() == "rebuild":
            try:
                from llm_code.runtime.indexer import ProjectIndexer
                self._app._project_index = ProjectIndexer(self._app._cwd).build_index()
                idx = self._app._project_index
                chat.add_entry(AssistantText(f"Index rebuilt: {len(idx.files)} files, {len(idx.symbols)} symbols"))
            except Exception as exc:
                chat.add_entry(AssistantText(f"Index rebuild failed: {exc}"))
        elif self._app._project_index:
            lines = [f"Files: {len(self._app._project_index.files)}, Symbols: {len(self._app._project_index.symbols)}"]
            for s in self._app._project_index.symbols[:20]:
                lines.append(f"  {s.kind} {s.name} — {s.file}:{s.line}")
            chat.add_entry(AssistantText("\n".join(lines)))
        else:
            chat.add_entry(AssistantText("No index available."))

    def _cmd_thinking(self, args: str) -> None:
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)
        if args in ("on", "off", "adaptive"):
            import dataclasses
            mode_map = {"on": "enabled", "off": "disabled", "adaptive": "adaptive"}
            new_mode = mode_map[args]
            from llm_code.runtime.config import ThinkingConfig
            new_thinking = ThinkingConfig(mode=new_mode, budget_tokens=self._app._config.thinking.budget_tokens)
            self._app._config = dataclasses.replace(self._app._config, thinking=new_thinking)
            if self._app._runtime:
                self._app._runtime._config = self._app._config
            chat.add_entry(AssistantText(f"Thinking mode: {new_mode}"))
        else:
            current = self._app._config.thinking.mode if self._app._config else "unknown"
            chat.add_entry(AssistantText(f"Thinking: {current}\nUsage: /thinking [adaptive|on|off]"))

    def _cmd_vim(self, args: str) -> None:
        from llm_code.tui.chat_view import ChatScrollView, AssistantText
        from llm_code.tui.input_bar import InputBar
        from llm_code.tui.status_bar import StatusBar

        chat = self._app.query_one(ChatScrollView)
        input_bar = self._app.query_one(InputBar)
        status_bar = self._app.query_one(StatusBar)
        if input_bar.vim_mode:
            input_bar.vim_mode = ""
            status_bar.vim_mode = ""
            chat.add_entry(AssistantText("Vim mode disabled"))
        else:
            input_bar.vim_mode = "NORMAL"
            status_bar.vim_mode = "NORMAL"
            chat.add_entry(AssistantText("Vim mode enabled"))

    def _cmd_image(self, args: str) -> None:
        from llm_code.tui.chat_view import ChatScrollView, AssistantText
        from llm_code.tui.input_bar import InputBar

        chat = self._app.query_one(ChatScrollView)
        input_bar = self._app.query_one(InputBar)
        if not args:
            chat.add_entry(AssistantText("Usage: /image <path>"))
            return
        try:
            from llm_code.cli.image import load_image_from_path
            img_path = Path(args).expanduser().resolve()
            img = load_image_from_path(str(img_path))
            self._app._pending_images.append(img)
            input_bar.insert_image_marker()
        except FileNotFoundError:
            chat.add_entry(AssistantText(f"Image not found: {args}"))

    def _cmd_lsp(self, args: str) -> None:
        from llm_code.tui.chat_view import ChatScrollView, AssistantText
        self._app.query_one(ChatScrollView).add_entry(AssistantText("LSP: not started in this session."))

    def _cmd_cancel(self, args: str) -> None:
        from llm_code.tui.chat_view import ChatScrollView, AssistantText
        if self._app._runtime and hasattr(self._app._runtime, '_cancel'):
            self._app._runtime._cancel()
        self._app.query_one(ChatScrollView).add_entry(AssistantText("(cancelled)"))

    def _cmd_plan(self, args: str) -> None:
        """Toggle plan/act mode."""
        from llm_code.tui.chat_view import ChatScrollView, AssistantText
        from llm_code.tui.status_bar import StatusBar

        self._app._plan_mode = not self._app._plan_mode
        status = self._app.query_one(StatusBar)
        chat = self._app.query_one(ChatScrollView)
        if self._app._plan_mode:
            status.plan_mode = "PLAN"
            chat.add_entry(AssistantText(
                "Plan mode ON -- agent will explore and plan without making changes."
            ))
        else:
            status.plan_mode = ""
            chat.add_entry(AssistantText(
                "Plan mode OFF -- back to normal."
            ))
        if self._app._runtime:
            self._app._runtime.plan_mode = self._app._plan_mode

    def _cmd_yolo(self, args: str) -> None:
        """Toggle YOLO mode — auto-accept all permission prompts.

        Equivalent to --dangerously-skip-permissions in Claude Code.
        """
        from llm_code.runtime.permissions import PermissionMode
        from llm_code.tui.chat_view import ChatScrollView, AssistantText
        from llm_code.tui.status_bar import StatusBar

        chat = self._app.query_one(ChatScrollView)
        status = self._app.query_one(StatusBar)

        if self._app._runtime is None or self._app._runtime._permissions is None:
            chat.add_entry(AssistantText("Runtime not initialized."))
            return

        policy = self._app._runtime._permissions
        # Toggle: if already in AUTO_ACCEPT, switch to PROMPT; otherwise enable YOLO
        current_mode = getattr(policy, "_mode", PermissionMode.PROMPT)
        if current_mode == PermissionMode.AUTO_ACCEPT:
            policy._mode = PermissionMode.PROMPT
            status.plan_mode = ""
            chat.add_entry(AssistantText(
                "YOLO mode OFF — permissions will prompt again."
            ))
        else:
            policy._mode = PermissionMode.AUTO_ACCEPT
            status.plan_mode = "YOLO"
            chat.add_entry(AssistantText(
                "YOLO mode ON — all permissions auto-accepted. "
                "⚠️  Be careful: write/delete operations will execute without confirmation."
            ))

    def _cmd_mode(self, args: str) -> None:
        """Switch between suggest/normal/plan modes."""
        from llm_code.runtime.permissions import PermissionMode
        from llm_code.tui.chat_view import ChatScrollView, AssistantText
        from llm_code.tui.status_bar import StatusBar

        chat = self._app.query_one(ChatScrollView)
        status = self._app.query_one(StatusBar)

        # Map mode names to PermissionMode values and status bar labels
        valid_modes = {
            "suggest": (PermissionMode.PROMPT, "SUGGEST"),
            "normal": (PermissionMode.WORKSPACE_WRITE, ""),
            "plan": (PermissionMode.PLAN, "PLAN"),
        }

        if not args.strip():
            # Determine current mode name from status bar state and plan flag
            if self._app._plan_mode:
                current = "plan"
            elif status.plan_mode == "SUGGEST":
                current = "suggest"
            else:
                current = "normal"
            chat.add_entry(AssistantText(
                f"Current mode: {current}\nAvailable: suggest, normal, plan"
            ))
            return

        mode_name = args.strip().lower()
        if mode_name not in valid_modes:
            chat.add_entry(AssistantText(
                f"Unknown mode: {mode_name}. Use: suggest, normal, plan"
            ))
            return

        perm_mode, label = valid_modes[mode_name]

        # Update plan mode flag
        self._app._plan_mode = mode_name == "plan"

        # Update status bar
        status.plan_mode = label

        # Update runtime permission policy mode
        if self._app._runtime and hasattr(self._app._runtime, "_permissions"):
            self._app._runtime._permissions._mode = perm_mode
        if self._app._runtime:
            self._app._runtime.plan_mode = self._app._plan_mode

        chat.add_entry(AssistantText(f"Switched to {mode_name} mode"))

    def _cmd_harness(self, args: str) -> None:
        """Show or configure harness controls."""
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)

        if not self._app._runtime or not hasattr(self._app._runtime, "_harness"):
            chat.add_entry(AssistantText("Harness not available."))
            return

        harness = self._app._runtime._harness
        parts = args.strip().split()

        if not parts:
            # Show status
            status = harness.status()
            lines = [f"Harness: {status['template']}\n"]
            lines.append("  Guides (feedforward):")
            for g in status["guides"]:
                mark = "✓" if g["enabled"] else "✗"
                lines.append(f"    {mark} {g['name']:<22} {g['trigger']:<12} {g['kind']}")
            lines.append("\n  Sensors (feedback):")
            for s in status["sensors"]:
                mark = "✓" if s["enabled"] else "✗"
                lines.append(f"    {mark} {s['name']:<22} {s['trigger']:<12} {s['kind']}")
            chat.add_entry(AssistantText("\n".join(lines)))
            return

        action = parts[0]
        if action == "enable" and len(parts) > 1:
            harness.enable(parts[1])
            chat.add_entry(AssistantText(f"Enabled: {parts[1]}"))
        elif action == "disable" and len(parts) > 1:
            harness.disable(parts[1])
            chat.add_entry(AssistantText(f"Disabled: {parts[1]}"))
        elif action == "template" and len(parts) > 1:
            from llm_code.harness.templates import default_controls
            from llm_code.harness.config import HarnessConfig
            new_controls = default_controls(parts[1])
            harness._config = HarnessConfig(template=parts[1], controls=new_controls)
            harness._overrides.clear()
            chat.add_entry(AssistantText(f"Switched to template: {parts[1]}"))
        else:
            chat.add_entry(AssistantText(
                "Usage: /harness [enable|disable|template] [name]\n"
                "  /harness              — show status\n"
                "  /harness enable X     — enable control X\n"
                "  /harness disable X    — disable control X\n"
                "  /harness template Y   — switch to template Y"
            ))

    def _cmd_knowledge(self, args: str) -> None:
        """View or rebuild the project knowledge base."""
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)

        parts = args.strip().split()
        action = parts[0] if parts else ""

        if action == "rebuild":
            import asyncio
            asyncio.ensure_future(self._rebuild_knowledge())
            return

        # Show knowledge index
        try:
            from llm_code.runtime.knowledge_compiler import KnowledgeCompiler
            compiler = KnowledgeCompiler(cwd=self._app._cwd, llm_provider=None)
            entries = compiler.get_index()
        except Exception:
            chat.add_entry(AssistantText("Knowledge base not available."))
            return

        if not entries:
            chat.add_entry(AssistantText(
                "Knowledge base is empty.\n"
                "It will be built automatically after your next session, "
                "or run `/knowledge rebuild` to build now."
            ))
            return

        lines = ["## Project Knowledge Base\n"]
        for entry in entries:
            lines.append(f"- **{entry.title}** — {entry.summary}")
        lines.append(f"\n{len(entries)} articles. Use `/knowledge rebuild` to force recompilation.")
        chat.add_entry(AssistantText("\n".join(lines)))

    async def _rebuild_knowledge(self) -> None:
        """Force full knowledge rebuild."""
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)
        if not self._app._runtime:
            chat.add_entry(AssistantText("Runtime not available."))
            return

        chat.add_entry(AssistantText("Rebuilding knowledge base..."))
        try:
            from llm_code.runtime.knowledge_compiler import KnowledgeCompiler
            compile_model = ""
            if hasattr(self._app._config, "knowledge"):
                compile_model = self._app._config.knowledge.compile_model
            if not compile_model and hasattr(self._app._config, "model_routing"):
                compile_model = self._app._config.model_routing.compaction
            compiler = KnowledgeCompiler(
                cwd=self._app._cwd,
                llm_provider=self._app._runtime._provider,
                compile_model=compile_model,
            )
            ingest_data = compiler.ingest(facts=[], since_commit=None)
            import asyncio
            await asyncio.wait_for(compiler.compile(ingest_data), timeout=60.0)
            entries = compiler.get_index()
            chat.add_entry(AssistantText(f"Knowledge base rebuilt: {len(entries)} articles."))
        except Exception as exc:
            chat.add_entry(AssistantText(f"Rebuild failed: {exc}"))

    def _cmd_dump(self, args: str) -> None:
        """Dump codebase for external LLM use (DAFC pattern)."""
        import asyncio
        asyncio.ensure_future(self._run_dump(args))

    async def _run_dump(self, args: str) -> None:
        from llm_code.tools.dump import dump_codebase
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)

        max_files = 200
        if args.strip().isdigit():
            max_files = int(args.strip())

        result = dump_codebase(self._app._cwd, max_files=max_files)

        if result.file_count == 0:
            chat.add_entry(AssistantText("No source files found to dump."))
            return

        # Write to file
        dump_path = self._app._cwd / ".llmcode" / "dump.txt"
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        dump_path.write_text(result.text, encoding="utf-8")

        chat.add_entry(AssistantText(
            f"Dumped {result.file_count} files "
            f"({result.total_lines:,} lines, ~{result.estimated_tokens:,} tokens)\n"
            f"Saved to: {dump_path}"
        ))

    def _cmd_analyze(self, args: str) -> None:
        """Run code analysis rules on the codebase."""
        import asyncio
        asyncio.ensure_future(self._run_analyze(args))

    async def _run_analyze(self, args: str) -> None:
        from llm_code.analysis.engine import run_analysis
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)

        target = Path(args.strip()) if args.strip() else self._app._cwd
        if not target.is_absolute():
            target = self._app._cwd / target

        try:
            result = run_analysis(target)
        except Exception as exc:
            chat.add_entry(AssistantText(f"Analysis failed: {exc}"))
            return

        chat.add_entry(AssistantText(result.format_chat()))

        # Store context for injection into future prompts
        if result.violations:
            self._app._analysis_context = result.format_context(max_tokens=1000)
            if self._app._runtime is not None:
                self._app._runtime.analysis_context = self._app._analysis_context
        else:
            self._app._analysis_context = None
            if self._app._runtime is not None:
                self._app._runtime.analysis_context = None

    def _cmd_diff_check(self, args: str) -> None:
        """Show new and fixed violations compared with cached results."""
        import asyncio
        asyncio.ensure_future(self._run_diff_check(args))

    async def _run_diff_check(self, args: str) -> None:
        from llm_code.analysis.engine import run_diff_check
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)

        try:
            new_violations, fixed_violations = run_diff_check(self._app._cwd)
        except Exception as exc:
            chat.add_entry(AssistantText(f"Diff check failed: {exc}"))
            return

        if not new_violations and not fixed_violations:
            chat.add_entry(AssistantText("No changes in violations since last analysis."))
            return

        lines: list[str] = ["## Diff Check"]
        for v in new_violations:
            loc = f"{v.file_path}:{v.line}" if v.line > 0 else v.file_path
            lines.append(f"NEW {v.severity.upper()} {loc} {v.message}")
        for v in fixed_violations:
            loc = f"{v.file_path}:{v.line}" if v.line > 0 else v.file_path
            lines.append(f"FIXED {v.severity.upper()} {loc} {v.message}")

        lines.append(f"\n{len(new_violations)} new, {len(fixed_violations)} fixed")
        chat.add_entry(AssistantText("\n".join(lines)))

    def _cmd_search(self, args: str) -> None:
        """Cross-session full-text search via SQLite FTS5 + current session fallback."""
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)
        if not args:
            chat.add_entry(AssistantText("Usage: /search <query>"))
            return

        lines: list[str] = []

        # 1. Search across ALL sessions via SQLite FTS5
        try:
            from llm_code.runtime.conversation_db import ConversationDB
            db = ConversationDB()
            # Escape FTS5 special chars to prevent syntax errors
            safe_query = self._escape_fts5(args)
            db_results = db.search(safe_query, limit=20)
            for r in db_results:
                session_label = r.conversation_name or r.conversation_id[:8]
                date_str = r.created_at[:10] if r.created_at else ""
                snippet = r.content_snippet.replace(">>>", "**").replace("<<<", "**")
                role_icon = ">" if r.role == "user" else "<"
                lines.append(f"  {role_icon} [{date_str}] ({session_label}) {snippet}")
            db.close()
        except Exception:
            pass

        # 2. Fallback: search current session in-memory
        if not lines and self._app._runtime:
            for msg in self._app._runtime.session.messages:
                text = " ".join(
                    getattr(b, "text", "") for b in msg.content
                    if hasattr(b, "text")
                )
                if args.lower() in text.lower():
                    role_icon = ">" if msg.role == "user" else "<"
                    lines.append(f"  {role_icon} [current] {text[:120]}")

        if lines:
            header = f"Found {len(lines)} match(es) for \"{args}\""
            if len(lines) > 20:
                header += " (showing first 20)"
            chat.add_entry(AssistantText(header + ":\n" + "\n".join(lines[:20])))
        else:
            chat.add_entry(AssistantText(f"No matches for: {args}"))

    def _cmd_set(self, args: str) -> None:
        """Set a config value: /set temperature 0.5"""
        from llm_code.tui.chat_view import ChatScrollView, AssistantText
        from llm_code.tui.header_bar import HeaderBar

        chat = self._app.query_one(ChatScrollView)
        parts = args.strip().split(None, 1)
        if len(parts) < 2:
            from llm_code.tui.settings_modal import editable_fields
            chat.add_entry(AssistantText(
                f"Usage: /set <key> <value>\nEditable: {', '.join(sorted(editable_fields()))}"
            ))
            return
        key, value = parts[0], parts[1]
        try:
            from llm_code.tui.settings_modal import apply_setting
            self._app._config = apply_setting(self._app._config, key, value)
            if key == "model":
                self._app._init_runtime()
                self._app.query_one(HeaderBar).model = value.strip()
            chat.add_entry(AssistantText(f"Set {key} = {value}"))
        except ValueError as exc:
            chat.add_entry(AssistantText(f"Error: {exc}"))

    def _cmd_settings(self, args: str) -> None:
        """Open the settings panel."""
        from textual.screen import ModalScreen
        from textual.containers import VerticalScroll
        from textual.widgets import Static
        from llm_code.tui.settings_modal import (
            build_settings_sections, render_sections_text,
        )

        runtime_like = type("_RT", (), {
            "model": getattr(self._app._config, "model", "") if self._app._config else "",
            "permission_mode": getattr(self._app._config, "permission_mode", "") if self._app._config else "",
            "plan_mode": self._app._plan_mode,
            "config": self._app._config,
            "cost_tracker": self._app._cost_tracker,
            "keybindings": None,
            "active_skills": [],
        })()
        sections = build_settings_sections(runtime_like)
        body = render_sections_text(sections)

        class SettingsScreen(ModalScreen):
            DEFAULT_CSS = """
            SettingsScreen { align: center middle; }
            #settings-box {
                width: 80%;
                height: 80%;
                background: $surface;
                border: round $accent;
                padding: 1 2;
            }
            #settings-footer { dock: bottom; height: 1; color: $text-muted; text-align: center; }
            """

            def compose(self):
                with VerticalScroll(id="settings-box"):
                    yield Static(body)
                yield Static("Esc close", id="settings-footer")

            def on_key(self, event) -> None:
                if event.key == "escape":
                    self.dismiss()
                    event.prevent_default()
                    event.stop()

        self._app.push_screen(SettingsScreen())

    def _cmd_config(self, args: str) -> None:
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)
        if not self._app._config:
            chat.add_entry(AssistantText("No config loaded."))
            return
        lines = [
            f"model: {self._app._config.model}",
            f"provider: {self._app._config.provider_base_url or 'default'}",
            f"permission: {self._app._config.permission_mode}",
            f"thinking: {self._app._config.thinking.mode}",
        ]
        chat.add_entry(AssistantText("\n".join(lines)))

    def _cmd_session(self, args: str) -> None:
        from llm_code.tui.chat_view import ChatScrollView, AssistantText
        self._app.query_one(ChatScrollView).add_entry(AssistantText("Session management: use /session list|save"))

    # ── Voice ─────────────────────────────────────────────────────────

    def _cmd_voice(self, args: str) -> None:
        """Start/stop microphone capture and run STT on the result.

        `/voice on`  → detect recording backend, build STT engine from config,
                      start the recorder and flip `_voice_active`.
        `/voice off` → stop the recorder, dispatch a worker that transcribes
                      the PCM in a thread and inserts the text into the input
                      bar on the UI thread.
        `/voice`     → report current status / backend / usage.
        """
        from llm_code.tui.chat_view import AssistantText, ChatScrollView

        chat = self._app.query_one(ChatScrollView)
        arg = args.strip().lower()

        cfg = (
            getattr(self._app._config, "voice", None)
            if self._app._config is not None
            else None
        )
        if cfg is None or not cfg.enabled:
            chat.add_entry(AssistantText(
                "Voice not configured. Set `voice.enabled = true` in "
                "config.json and pick a backend: `local` (on-device "
                "faster-whisper, no server), `whisper` (HTTP endpoint), "
                "`google`, or `anthropic`."
            ))
            return

        if arg == "on":
            if self._app._voice_active:
                chat.add_entry(AssistantText("Voice already recording. Run `/voice off` to stop."))
                return

            try:
                from llm_code.voice.recorder import AudioRecorder, detect_backend
                backend = detect_backend()
                recorder = AudioRecorder(backend=backend)
            except Exception as exc:
                chat.add_entry(AssistantText(f"Voice recorder init failed: {exc}"))
                return

            if self._app._voice_stt is None:
                try:
                    from llm_code.voice.stt import create_stt_engine
                    self._app._voice_stt = create_stt_engine(cfg)
                except Exception as exc:
                    chat.add_entry(AssistantText(f"Voice STT init failed: {exc}"))
                    return

            try:
                recorder.start()
            except Exception as exc:
                chat.add_entry(AssistantText(f"Voice recording failed to start: {exc}"))
                return

            self._app._voice_recorder = recorder
            self._app._voice_active = True
            chat.add_entry(AssistantText(
                "🎤 Recording — run `/voice off` to stop and transcribe."
            ))
            return

        if arg == "off":
            recorder = self._app._voice_recorder
            stt_engine = self._app._voice_stt
            if not self._app._voice_active or recorder is None:
                chat.add_entry(AssistantText("Voice is not recording."))
                return

            self._app._voice_active = False
            self._app._voice_recorder = None

            try:
                audio_bytes = recorder.stop()
            except Exception as exc:
                chat.add_entry(AssistantText(f"Voice stop failed: {exc}"))
                return

            if not audio_bytes:
                chat.add_entry(AssistantText("No audio captured."))
                return

            duration = len(audio_bytes) / (2 * 16000)  # 16-bit @ 16kHz
            chat.add_entry(AssistantText(
                f"🎤 Transcribing {duration:.1f}s of audio…"
            ))
            self._app.run_worker(
                self._transcribe_voice(stt_engine, audio_bytes, cfg.language),
                name="voice_transcribe",
            )
            return

        # Bare `/voice` — status/help.
        state = "recording 🎤" if self._app._voice_active else "idle"
        chat.add_entry(AssistantText(
            f"Voice: {state}\n"
            f"Backend: {cfg.backend}\n"
            f"Language: {cfg.language}\n"
            f"Usage: /voice [on|off]"
        ))

    async def _transcribe_voice(
        self,
        stt_engine: "Any",
        audio_bytes: bytes,
        language: str,
    ) -> None:
        """Run blocking STT in a worker thread, then insert the transcript."""
        import asyncio

        from llm_code.tui.chat_view import AssistantText, ChatScrollView
        from llm_code.tui.input_bar import InputBar

        chat = self._app.query_one(ChatScrollView)

        try:
            text = await asyncio.to_thread(
                stt_engine.transcribe, audio_bytes, language
            )
        except Exception as exc:
            chat.add_entry(AssistantText(f"STT failed: {exc}"))
            return

        text = (text or "").strip()
        if not text:
            chat.add_entry(AssistantText("STT returned an empty transcript."))
            return

        try:
            input_bar = self._app.query_one(InputBar)
            input_bar.insert_text(text + " ")
        except Exception as exc:
            chat.add_entry(AssistantText(
                f"Transcribed: {text}\n(Insertion failed: {exc})"
            ))

    # ── Cron ──────────────────────────────────────────────────────────

    def _cmd_cron(self, args: str) -> None:
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)
        if self._app._cron_storage is None:
            chat.add_entry(AssistantText("Cron not available."))
            return
        sub = args.strip() if args.strip() else "list"
        if not sub or sub == "list":
            tasks = self._app._cron_storage.list_all()
            if not tasks:
                chat.add_entry(AssistantText("No scheduled tasks."))
            else:
                lines = [f"Scheduled tasks ({len(tasks)}):"]
                for t in tasks:
                    flags = []
                    if t.recurring:
                        flags.append("recurring")
                    if t.permanent:
                        flags.append("permanent")
                    flag_str = f" [{', '.join(flags)}]" if flags else ""
                    fired = f", last fired: {t.last_fired_at:%Y-%m-%d %H:%M}" if t.last_fired_at else ""
                    lines.append(f"  {t.id}  {t.cron}  \"{t.prompt}\"{flag_str}{fired}")
                chat.add_entry(AssistantText("\n".join(lines)))
        elif sub.startswith("delete "):
            task_id = sub.split(None, 1)[1].strip()
            removed = self._app._cron_storage.remove(task_id)
            if removed:
                chat.add_entry(AssistantText(f"Deleted task {task_id}"))
            else:
                chat.add_entry(AssistantText(f"Task '{task_id}' not found"))
        elif sub == "add":
            chat.add_entry(AssistantText(
                "Use the cron_create tool to schedule a task:\n"
                "  cron: '0 9 * * *'  (5-field cron expression)\n"
                "  prompt: 'your prompt here'\n"
                "  recurring: true/false\n"
                "  permanent: true/false"
            ))
        else:
            chat.add_entry(AssistantText("Usage: /cron [list|add|delete <id>]"))

    # ── Task ──────────────────────────────────────────────────────────

    def _cmd_task(self, args: str) -> None:
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)
        parts = args.strip().split(None, 1)
        sub = parts[0] if parts else ""
        if sub in ("new", ""):
            chat.add_entry(AssistantText("Use the task tools directly to create or manage tasks."))
        elif sub == "list":
            if self._app._task_manager is None:
                chat.add_entry(AssistantText("Task manager not initialized."))
            else:
                try:
                    tasks = self._app._task_manager.list_tasks(exclude_done=False)
                    if not tasks:
                        chat.add_entry(AssistantText("No tasks found."))
                    else:
                        lines = ["Tasks:"]
                        for t in tasks:
                            lines.append(f"  {t.id}  [{t.status.value:8s}]  {t.title}")
                        chat.add_entry(AssistantText("\n".join(lines)))
                except Exception as exc:
                    chat.add_entry(AssistantText(f"Error listing tasks: {exc}"))
        elif sub in ("verify", "close"):
            chat.add_entry(AssistantText("Use the task tools directly."))
        else:
            chat.add_entry(AssistantText("Usage: /task [new|verify <id>|close <id>|list]"))

    # ── Swarm ─────────────────────────────────────────────────────────

    def _cmd_personas(self, args: str) -> None:
        """List available built-in agent personas for the swarm."""
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)
        from llm_code.swarm.personas import BUILTIN_PERSONAS

        lines = ["Available built-in personas:", ""]
        for name in sorted(BUILTIN_PERSONAS):
            persona = BUILTIN_PERSONAS[name]
            lines.append(f"  /{name:18s} — {persona.description}")
        chat.add_entry(AssistantText("\n".join(lines)))

    def _cmd_orchestrate(self, args: str) -> None:
        """Run the OrchestratorHook with inline LLM execution per persona."""
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)
        task = args.strip()
        if not task:
            chat.add_entry(AssistantText(
                "Usage: /orchestrate <task description>\n"
                "Routes the task to a persona by category and retries with "
                "fallback personas on failure."
            ))
            return
        if self._app._runtime is None:
            chat.add_entry(AssistantText("Orchestrate: runtime not ready."))
            return
        self._app.run_worker(self._run_orchestrate(task), name="orchestrate")

    async def _run_orchestrate(self, task: str) -> None:
        from llm_code.tui.chat_view import ChatScrollView, AssistantText, SkillBadge

        chat = self._app.query_one(ChatScrollView)
        try:
            from llm_code.swarm.orchestrator_hook import OrchestratorHook, categorize
            from llm_code.runtime.orchestrate_executor import (
                make_inline_persona_executor,
                sync_wrap,
            )

            runtime = self._app._runtime
            executor = make_inline_persona_executor(runtime)
            hook = OrchestratorHook(executor=sync_wrap(executor))
            # Run blocking orchestrate in thread to avoid blocking UI loop.
            import asyncio
            result = await asyncio.to_thread(hook.orchestrate, task)
            category = categorize(task)

            success_attempt = next((a for a in result.attempts if a.success), None)
            if success_attempt is not None:
                chat.add_entry(SkillBadge([success_attempt.persona]))
                chat.add_entry(AssistantText(result.final_output))
            else:
                lines = [f"Orchestrate failed (category={category}):", ""]
                for a in result.attempts:
                    lines.append(f"  attempt {a.attempt}: {a.persona} -> FAIL: {a.error}")
                chat.add_entry(AssistantText("\n".join(lines)))
        except Exception as exc:
            chat.add_entry(AssistantText(f"Orchestrate failed: {exc}"))

    def _cmd_swarm(self, args: str) -> None:
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)
        parts = args.strip().split(None, 1)
        sub = parts[0] if parts else ""
        rest = parts[1].strip() if len(parts) > 1 else ""
        if sub == "coordinate":
            if not rest:
                chat.add_entry(AssistantText("Usage: /swarm coordinate <task>"))
                return
            chat.add_entry(AssistantText("Swarm coordination: use the swarm tools directly."))
        else:
            if self._app._swarm_manager is None:
                chat.add_entry(AssistantText("Swarm: not enabled. Set swarm.enabled=true in config."))
            else:
                chat.add_entry(AssistantText("Swarm: active\nUsage: /swarm coordinate <task>"))

    # ── VCR ───────────────────────────────────────────────────────────

    def _cmd_vcr(self, args: str) -> None:
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)
        sub = args.strip().split(None, 1)[0] if args.strip() else ""
        if sub == "start":
            if self._app._vcr_recorder is not None:
                chat.add_entry(AssistantText("VCR recording already active."))
                return
            try:
                import uuid
                from llm_code.runtime.vcr import VCRRecorder
                recordings_dir = Path.home() / ".llmcode" / "recordings"
                recordings_dir.mkdir(parents=True, exist_ok=True)
                session_id = uuid.uuid4().hex[:8]
                path = recordings_dir / f"{session_id}.jsonl"
                self._app._vcr_recorder = VCRRecorder(path)
                if self._app._runtime is not None:
                    self._app._runtime._vcr_recorder = self._app._vcr_recorder
                chat.add_entry(AssistantText(f"VCR recording started: {path.name}"))
            except Exception as exc:
                chat.add_entry(AssistantText(f"VCR start failed: {exc}"))
        elif sub == "stop":
            if self._app._vcr_recorder is None:
                chat.add_entry(AssistantText("No active VCR recording."))
                return
            self._app._vcr_recorder.close()
            self._app._vcr_recorder = None
            if self._app._runtime is not None:
                self._app._runtime._vcr_recorder = None
            chat.add_entry(AssistantText("VCR recording stopped."))
        elif sub == "list":
            recordings_dir = Path.home() / ".llmcode" / "recordings"
            if not recordings_dir.is_dir():
                chat.add_entry(AssistantText("No recordings found."))
                return
            files = sorted(recordings_dir.glob("*.jsonl"))
            if not files:
                chat.add_entry(AssistantText("No recordings found."))
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
                chat.add_entry(AssistantText("\n".join(lines)))
            except Exception as exc:
                chat.add_entry(AssistantText(f"VCR list failed: {exc}"))
        else:
            active = "active" if self._app._vcr_recorder is not None else "inactive"
            chat.add_entry(AssistantText(f"VCR: {active}\nUsage: /vcr start|stop|list"))

    # ── Checkpoint ────────────────────────────────────────────────────

    def _cmd_checkpoint(self, args: str) -> None:
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)
        try:
            from llm_code.runtime.checkpoint_recovery import CheckpointRecovery
        except ImportError:
            chat.add_entry(AssistantText("Checkpoint recovery not available."))
            return
        checkpoints_dir = Path.home() / ".llmcode" / "checkpoints"
        recovery = CheckpointRecovery(checkpoints_dir)
        parts = args.strip().split(None, 1)
        sub = parts[0].lower() if parts else "list"
        rest = parts[1].strip() if len(parts) > 1 else ""
        if sub == "save":
            if self._app._runtime is None:
                chat.add_entry(AssistantText("No active session to checkpoint."))
                return
            try:
                path = recovery.save_checkpoint(self._app._runtime.session)
                chat.add_entry(AssistantText(f"Checkpoint saved: {path}"))
            except Exception as exc:
                chat.add_entry(AssistantText(f"Save failed: {exc}"))
        elif sub in ("list", ""):
            try:
                entries = recovery.list_checkpoints()
                if not entries:
                    chat.add_entry(AssistantText("No checkpoints found."))
                    return
                lines = ["Checkpoints:"]
                for e in entries:
                    lines.append(
                        f"  {e['session_id']}  "
                        f"{e['saved_at'][:19]}  "
                        f"({e['message_count']} msgs)  "
                        f"{e['project_path']}"
                    )
                chat.add_entry(AssistantText("\n".join(lines)))
            except Exception as exc:
                chat.add_entry(AssistantText(f"List failed: {exc}"))
        elif sub == "resume":
            try:
                session_id = rest or None
                # Wave2-2: pass the live cost_tracker so its running
                # token / cost totals pick up where the saved session
                # left off instead of resetting to zero.
                cost_tracker = self._app._cost_tracker
                if session_id:
                    session = recovery.load_checkpoint(
                        session_id, cost_tracker=cost_tracker,
                    )
                else:
                    session = recovery.detect_last_checkpoint(
                        cost_tracker=cost_tracker,
                    )
                if session is None:
                    chat.add_entry(AssistantText("No checkpoint found to resume."))
                    return
                self._app._init_runtime()
                chat.add_entry(AssistantText(
                    f"Resumed session {session.id} ({len(session.messages)} messages)"
                ))
            except Exception as exc:
                chat.add_entry(AssistantText(f"Resume failed: {exc}"))
        else:
            chat.add_entry(AssistantText("Usage: /checkpoint [save|list|resume [session_id]]"))

    # ── Memory ────────────────────────────────────────────────────────

    def _cmd_memory(self, args: str) -> None:
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)
        if not self._app._memory:
            chat.add_entry(AssistantText("Memory not initialized."))
            return
        parts = args.strip().split(None, 2)
        sub = parts[0] if parts else ""
        try:
            if sub == "set" and len(parts) > 2:
                self._app._memory.store(parts[1], parts[2])
                chat.add_entry(AssistantText(f"Stored: {parts[1]}"))
            elif sub == "get" and len(parts) > 1:
                val = self._app._memory.recall(parts[1])
                if val:
                    chat.add_entry(AssistantText(str(val)))
                else:
                    chat.add_entry(AssistantText(f"Key not found: {parts[1]}"))
            elif sub == "delete" and len(parts) > 1:
                self._app._memory.delete(parts[1])
                chat.add_entry(AssistantText(f"Deleted: {parts[1]}"))
            elif sub == "consolidate":
                chat.add_entry(AssistantText("Use --lite mode for consolidate (requires async)."))
            elif sub == "history":
                summaries = self._app._memory.load_consolidated_summaries(limit=5)
                if not summaries:
                    chat.add_entry(AssistantText("No consolidated memories yet."))
                else:
                    lines = [f"Consolidated Memories ({len(summaries)} most recent)"]
                    for i, s in enumerate(summaries):
                        preview = "\n".join(s.strip().splitlines()[:3])
                        lines.append(f"  #{i+1} {preview}")
                    chat.add_entry(AssistantText("\n".join(lines)))
            elif sub == "lint":
                flags = parts[1] if len(parts) > 1 else ""
                if "--deep" in flags:
                    import asyncio
                    asyncio.ensure_future(self._memory_lint_deep())
                elif "--fix" in flags:
                    import asyncio
                    asyncio.ensure_future(self._memory_lint_fix())
                else:
                    self._memory_lint_fast()
            else:
                entries = self._app._memory.get_all()
                lines = [f"Memory ({len(entries)} entries)"]
                for k, v in entries.items():
                    lines.append(f"  {k}: {v.value[:60]}")
                if not entries:
                    lines.append("  No memories stored.")
                chat.add_entry(AssistantText("\n".join(lines)))
        except Exception as exc:
            chat.add_entry(AssistantText(f"Memory error: {exc}"))

    def _memory_lint_fast(self) -> None:
        """Run fast computational memory lint."""
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)
        try:
            from llm_code.runtime.memory_validator import lint_memory
            result = lint_memory(memory_dir=self._app._memory._dir, cwd=self._app._cwd)
            report = result.format_report()
            if not result.stale and not result.coverage_gaps and not result.old:
                report += "\n\nContradictions: (requires LLM, skipped — use /memory lint --deep)"
            chat.add_entry(AssistantText(report))
        except Exception as exc:
            chat.add_entry(AssistantText(f"Lint failed: {exc}"))

    async def _memory_lint_deep(self) -> None:
        """Run deep memory lint with LLM contradiction detection."""
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)
        chat.add_entry(AssistantText("Running deep memory lint..."))
        try:
            from llm_code.runtime.memory_validator import lint_memory_deep
            provider = self._app._runtime._provider if self._app._runtime else None
            result = await lint_memory_deep(
                memory_dir=self._app._memory._dir,
                cwd=self._app._cwd,
                llm_provider=provider,
            )
            chat.add_entry(AssistantText(result.format_report()))
        except Exception as exc:
            chat.add_entry(AssistantText(f"Deep lint failed: {exc}"))

    async def _memory_lint_fix(self) -> None:
        """Run lint and auto-remove stale references."""
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)
        try:
            from llm_code.runtime.memory_validator import lint_memory
            result = lint_memory(memory_dir=self._app._memory._dir, cwd=self._app._cwd)
            if not result.stale:
                chat.add_entry(AssistantText("No stale references to fix."))
                return
            removed = 0
            for s in result.stale:
                self._app._memory.delete(s.key)
                removed += 1
            chat.add_entry(AssistantText(f"Removed {removed} stale entries.\n\n{result.format_report()}"))
        except Exception as exc:
            chat.add_entry(AssistantText(f"Lint fix failed: {exc}"))

    # ── Repo Map ─────────────────────────────────────────────────────

    def _cmd_map(self, args: str) -> None:
        """Show repo map."""
        from llm_code.runtime.repo_map import build_repo_map
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)

        try:
            repo_map = build_repo_map(self._app._cwd)
            compact = repo_map.to_compact(max_tokens=2000)
            if compact:
                chat.add_entry(AssistantText(f"# Repo Map\n{compact}"))
            else:
                chat.add_entry(AssistantText("No source files found."))
        except Exception as exc:
            chat.add_entry(AssistantText(f"Error building repo map: {exc}"))

    # ── MCP ───────────────────────────────────────────────────────────

    def _cmd_mcp(self, args: str) -> None:
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)
        parts = args.strip().split(None, 1)
        sub = parts[0] if parts else ""
        subargs = parts[1] if len(parts) > 1 else ""
        if sub == "install" and subargs:
            pkg = subargs.strip()
            short_name = pkg.split("/")[-1] if "/" in pkg else pkg
            # Write to config.json
            config_path = Path.home() / ".llmcode" / "config.json"
            try:
                import json
                config_data: dict = {}
                if config_path.exists():
                    config_data = json.loads(config_path.read_text())
                mcp_servers = config_data.setdefault("mcp_servers", {})
                mcp_servers[short_name] = {"command": "npx", "args": ["-y", pkg]}
                config_path.parent.mkdir(parents=True, exist_ok=True)
                config_path.write_text(json.dumps(config_data, indent=2) + "\n")
                # Update in-memory config so marketplace reflects the change
                if self._app._config is not None:
                    import dataclasses
                    current_servers = dict(self._app._config.mcp_servers or {})
                    current_servers[short_name] = {"command": "npx", "args": ["-y", pkg]}
                    self._app._config = dataclasses.replace(self._app._config, mcp_servers=current_servers)
                chat.add_entry(AssistantText(f"Added {short_name} to config. Starting server..."))
                # Hot-start the MCP server without restart
                self._app._hot_start_mcp(short_name, {"command": "npx", "args": ["-y", pkg]})
            except Exception as exc:
                chat.add_entry(AssistantText(f"Install failed: {exc}"))
        elif sub == "remove" and subargs:
            name = subargs.strip()
            config_path = Path.home() / ".llmcode" / "config.json"
            try:
                import json
                if config_path.exists():
                    config_data = json.loads(config_path.read_text())
                    mcp_servers = config_data.get("mcp_servers", {})
                    if name in mcp_servers:
                        del mcp_servers[name]
                        config_path.write_text(json.dumps(config_data, indent=2) + "\n")
                        # Update in-memory config
                        if self._app._config is not None:
                            import dataclasses
                            current = dict(self._app._config.mcp_servers or {})
                            current.pop(name, None)
                            self._app._config = dataclasses.replace(self._app._config, mcp_servers=current)
                        chat.add_entry(AssistantText(f"Removed {name} from config."))
                    else:
                        chat.add_entry(AssistantText(f"MCP server '{name}' not found in config."))
                else:
                    chat.add_entry(AssistantText("No config file found."))
            except Exception as exc:
                chat.add_entry(AssistantText(f"Remove failed: {exc}"))
        else:
            # Open interactive MCP marketplace browser
            from llm_code.tui.marketplace import MarketplaceBrowser, MarketplaceItem

            items: list[MarketplaceItem] = []
            configured: set[str] = set()

            # Configured MCP servers
            servers = {}
            if self._app._config and self._app._config.mcp_servers:
                servers = self._app._config.mcp_servers
            for name, cfg in servers.items():
                configured.add(name)
                cmd = ""
                if isinstance(cfg, dict):
                    cmd = f"{cfg.get('command', '')} {' '.join(cfg.get('args', []))}".strip()
                items.append(MarketplaceItem(
                    name=name,
                    description=cmd or "(configured)",
                    source="configured",
                    installed=True,
                    enabled=True,
                    repo="",
                ))

            # Known MCP servers from npm registry (popular ones)
            known_mcp = [
                ("@anthropic/mcp-server-filesystem", "File system access via MCP"),
                ("@anthropic/mcp-server-github", "GitHub API integration via MCP"),
                ("@anthropic/mcp-server-slack", "Slack integration via MCP"),
                ("@anthropic/mcp-server-google-maps", "Google Maps API via MCP"),
                ("@anthropic/mcp-server-puppeteer", "Browser automation via MCP"),
                ("@anthropic/mcp-server-memory", "Persistent memory via MCP"),
                ("@anthropic/mcp-server-postgres", "PostgreSQL access via MCP"),
                ("@anthropic/mcp-server-sqlite", "SQLite database via MCP"),
                ("@modelcontextprotocol/server-brave-search", "Brave search via MCP"),
                ("@modelcontextprotocol/server-fetch", "HTTP fetch via MCP"),
                ("tavily-mcp", "Tavily AI search via MCP"),
                ("@supabase/mcp-server-supabase", "Supabase database via MCP"),
                ("context7-mcp", "Context7 documentation lookup via MCP"),
            ]
            for pkg_name, desc in known_mcp:
                short = pkg_name.split("/")[-1] if "/" in pkg_name else pkg_name
                if short not in configured and pkg_name not in configured:
                    items.append(MarketplaceItem(
                        name=pkg_name,
                        description=desc,
                        source="npm",
                        installed=False,
                        repo="",
                        extra="npx",
                    ))

            browser = MarketplaceBrowser("MCP Server Marketplace", items)
            self._app.push_screen(browser)

    # ── IDE ───────────────────────────────────────────────────────────

    def _cmd_ide(self, args: str) -> None:
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)
        sub = args.strip().lower()
        if sub == "connect":
            chat.add_entry(AssistantText("IDE bridge starts automatically when configured. Set ide.enabled=true in config."))
            return
        # status (default)
        if self._app._ide_bridge is None:
            chat.add_entry(AssistantText("IDE integration is disabled. Set ide.enabled=true in config."))
            return
        try:
            if self._app._ide_bridge.is_connected:
                ides = self._app._ide_bridge._server.connected_ides if self._app._ide_bridge._server else []
                names = ", ".join(ide.name for ide in ides) if ides else "unknown"
                chat.add_entry(AssistantText(f"IDE connected: {names}"))
            else:
                port = self._app._ide_bridge._config.port
                chat.add_entry(AssistantText(f"IDE bridge listening on port {port}, no IDE connected."))
        except Exception as exc:
            chat.add_entry(AssistantText(f"IDE status error: {exc}"))

    # ── HIDA ──────────────────────────────────────────────────────────

    def _cmd_hida(self, args: str) -> None:
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)
        if self._app._runtime and hasattr(self._app._runtime, "_last_hida_profile"):
            profile = self._app._runtime._last_hida_profile
            if profile is not None:
                try:
                    from llm_code.runtime.hida import HidaEngine
                    engine = HidaEngine()
                    summary = engine.build_summary(profile)
                    chat.add_entry(AssistantText(f"HIDA: {summary}"))
                except Exception as exc:
                    chat.add_entry(AssistantText(f"HIDA: {exc}"))
            else:
                hida_enabled = (
                    getattr(self._app._config, "hida", None) and self._app._config.hida.enabled
                )
                status = "enabled" if hida_enabled else "disabled"
                chat.add_entry(AssistantText(f"HIDA: {status}, no classification yet"))
        else:
            chat.add_entry(AssistantText("HIDA: not initialized"))

    # ── Skill ─────────────────────────────────────────────────────────

    def _cmd_skill(self, args: str) -> None:
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)
        parts = args.strip().split(None, 1)
        sub = parts[0] if parts else ""
        subargs = parts[1] if len(parts) > 1 else ""
        if sub == "install" and subargs:
            source = subargs.strip()
            if not self._is_valid_repo(source):
                chat.add_entry(AssistantText("Usage: /skill install owner/repo"))
                return
            import tempfile
            repo = source.replace("https://github.com/", "").rstrip("/")
            name = repo.split("/")[-1]
            dest = Path.home() / ".llmcode" / "skills" / name
            if dest.exists():
                shutil.rmtree(dest)
            chat.add_entry(AssistantText(f"Cloning {repo}..."))
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    result = subprocess.run(
                        ["git", "clone", "--depth", "1",
                         f"https://github.com/{repo}.git", tmp],
                        capture_output=True, text=True, timeout=30,
                    )
                    if result.returncode == 0:
                        skills_src = Path(tmp) / "skills"
                        if skills_src.is_dir():
                            shutil.copytree(skills_src, dest)
                        else:
                            shutil.copytree(tmp, dest)
                        self._app._reload_skills()
                        chat.add_entry(AssistantText(f"Installed {name}. Activated."))
                    else:
                        logger.warning("Skill clone failed for %s: %s", repo, result.stderr[:200])
                        chat.add_entry(AssistantText("Clone failed. Check the repository URL."))
            except Exception as exc:
                chat.add_entry(AssistantText(f"Install failed: {exc}"))
        elif sub == "enable" and subargs:
            if not self._is_safe_name(subargs):
                chat.add_entry(AssistantText("Invalid skill name."))
                return
            marker = Path.home() / ".llmcode" / "skills" / subargs / ".disabled"
            marker.unlink(missing_ok=True)
            self._app._reload_skills()
            chat.add_entry(AssistantText(f"Enabled {subargs}"))
        elif sub == "disable" and subargs:
            if not self._is_safe_name(subargs):
                chat.add_entry(AssistantText("Invalid skill name."))
                return
            marker = Path.home() / ".llmcode" / "skills" / subargs / ".disabled"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
            self._app._reload_skills()
            chat.add_entry(AssistantText(f"Disabled {subargs}"))
        elif sub == "remove" and subargs:
            if not self._is_safe_name(subargs):
                chat.add_entry(AssistantText("Invalid skill name."))
                return
            d = Path.home() / ".llmcode" / "skills" / subargs
            if d.is_dir():
                shutil.rmtree(d)
                self._app._reload_skills()
                chat.add_entry(AssistantText(f"Removed {subargs}"))
            else:
                chat.add_entry(AssistantText(f"Not found: {subargs}"))
        else:
            # Open interactive marketplace browser
            from llm_code.tui.marketplace import MarketplaceBrowser, MarketplaceItem
            from llm_code.marketplace.builtin_registry import get_all_known_plugins

            items: list[MarketplaceItem] = []
            installed_names: set[str] = set()

            # Installed skills (from runtime)
            all_skills: list = []
            if self._app._skills:
                all_skills = list(self._app._skills.auto_skills) + list(self._app._skills.command_skills)
            for s in all_skills:
                installed_names.add(s.name)
                tokens = len(s.content) // 4
                mode = "auto" if s.auto else f"/{s.trigger}"
                items.append(MarketplaceItem(
                    name=s.name,
                    description=f"{mode}  ~{tokens} tokens",
                    source="installed",
                    installed=True,
                    enabled=not (Path.home() / ".llmcode" / "skills" / s.name / ".disabled").exists(),
                    repo="",
                    extra=mode,
                ))

            # Installed plugins (check filesystem for newly installed)
            try:
                from llm_code.marketplace.installer import PluginInstaller
                pi = PluginInstaller(Path.home() / ".llmcode" / "plugins")
                for p in pi.list_installed():
                    if p.manifest.name not in installed_names:
                        installed_names.add(p.manifest.name)
                        items.append(MarketplaceItem(
                            name=p.manifest.name,
                            description=getattr(p.manifest, "description", ""),
                            source="installed",
                            installed=True,
                            enabled=p.enabled,
                            repo="",
                            extra=f"v{p.manifest.version}",
                        ))
            except Exception:
                pass

            # Marketplace plugins — not yet installed
            for p in get_all_known_plugins():
                if p["name"] not in installed_names:
                    skill_count = p.get("skills", 0)
                    extra = f"{skill_count} skills" if skill_count > 0 else p.get("type", "plugin")
                    items.append(MarketplaceItem(
                        name=p["name"],
                        description=p.get("desc", ""),
                        source=p.get("source", "official"),
                        installed=False,
                        repo=p.get("repo", ""),
                        extra=extra,
                    ))

            browser = MarketplaceBrowser("Skills Marketplace", items)
            self._app.push_screen(browser)

    # ── Plugin ────────────────────────────────────────────────────────

    def _cmd_plugin(self, args: str) -> None:
        from llm_code.tui.chat_view import ChatScrollView, AssistantText

        chat = self._app.query_one(ChatScrollView)
        parts = args.strip().split(None, 1)
        sub = parts[0] if parts else ""
        subargs = parts[1] if len(parts) > 1 else ""
        try:
            from llm_code.marketplace.installer import PluginInstaller
            installer = PluginInstaller(Path.home() / ".llmcode" / "plugins")
        except ImportError:
            chat.add_entry(AssistantText("Plugin system not available."))
            return
        if sub == "install" and subargs:
            source = subargs.strip()
            if not self._is_valid_repo(source):
                chat.add_entry(AssistantText("Usage: /plugin install owner/repo"))
                return
            repo = source.replace("https://github.com/", "").rstrip("/")
            name = repo.split("/")[-1]
            dest = Path.home() / ".llmcode" / "plugins" / name
            if dest.exists():
                shutil.rmtree(dest)
            chat.add_entry(AssistantText(f"Cloning {repo}..."))
            try:
                result = subprocess.run(
                    ["git", "clone", "--depth", "1",
                     f"https://github.com/{repo}.git", str(dest)],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode == 0:
                    installer.enable(name)
                    self._app._reload_skills()
                    # Wave2-5 wiring: load any Python tools the
                    # plugin declares via provides_tools manifest
                    self._app._load_plugin_tools(dest, chat)
                    chat.add_entry(AssistantText(f"Installed {name}. Activated."))
                else:
                    logger.warning("Plugin clone failed for %s: %s", repo, result.stderr[:200])
                    chat.add_entry(AssistantText("Clone failed. Check the repository URL."))
            except Exception as exc:
                chat.add_entry(AssistantText(f"Install failed: {exc}"))
        elif sub == "enable" and subargs:
            if not self._is_safe_name(subargs):
                chat.add_entry(AssistantText("Invalid plugin name."))
                return
            try:
                installer.enable(subargs)
                self._app._reload_skills()
                dest = Path.home() / ".llmcode" / "plugins" / subargs
                self._app._load_plugin_tools(dest, chat)
                chat.add_entry(AssistantText(f"Enabled {subargs}"))
            except Exception as exc:
                chat.add_entry(AssistantText(f"Enable failed: {exc}"))
        elif sub == "disable" and subargs:
            if not self._is_safe_name(subargs):
                chat.add_entry(AssistantText("Invalid plugin name."))
                return
            try:
                self._app._unload_plugin_tools(subargs, chat)
                installer.disable(subargs)
                self._app._reload_skills()
                chat.add_entry(AssistantText(f"Disabled {subargs}"))
            except Exception as exc:
                chat.add_entry(AssistantText(f"Disable failed: {exc}"))
        elif sub in ("remove", "uninstall") and subargs:
            if not self._is_safe_name(subargs):
                chat.add_entry(AssistantText("Invalid plugin name."))
                return
            try:
                self._app._unload_plugin_tools(subargs, chat)
                installer.uninstall(subargs)
                self._app._reload_skills()
                chat.add_entry(AssistantText(f"Removed {subargs}"))
            except Exception as exc:
                chat.add_entry(AssistantText(f"Remove failed: {exc}"))
        else:
            # Open interactive marketplace browser
            from llm_code.tui.marketplace import MarketplaceBrowser, MarketplaceItem
            from llm_code.marketplace.builtin_registry import get_all_known_plugins

            items: list[MarketplaceItem] = []

            # Installed plugins first
            installed_names: set[str] = set()
            try:
                installed = installer.list_installed()
                for p in installed:
                    installed_names.add(p.manifest.name)
                    items.append(MarketplaceItem(
                        name=p.manifest.name,
                        description=getattr(p.manifest, "description", ""),
                        source="installed",
                        category="Installed",
                        installed=True,
                        enabled=p.enabled,
                        repo="",
                        extra=f"v{p.manifest.version}",
                    ))
            except Exception:
                pass

            # Known marketplace plugins not yet installed
            for p in get_all_known_plugins():
                if p["name"] not in installed_names:
                    skills_count = p.get("skills", 0)
                    extra = f"{skills_count} skills" if skills_count > 0 else p.get("type", "plugin")
                    _source = p.get("source", "official")
                    _category = "Official" if _source == "official" else "Community"
                    items.append(MarketplaceItem(
                        name=p["name"],
                        description=p.get("desc", ""),
                        source=_source,
                        category=_category,
                        installed=False,
                        repo=p.get("repo", ""),
                        extra=extra,
                    ))

            browser = MarketplaceBrowser("Plugin Marketplace", items)
            self._app.push_screen(browser)


# Wire the quit alias after the class body so it points to the same
# underlying function object as _cmd_exit.
CommandDispatcher._cmd_quit = CommandDispatcher._cmd_exit  # type: ignore[attr-defined]
