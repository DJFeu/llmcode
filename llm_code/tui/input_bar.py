"""InputBar — fixed bottom input with prompt, multiline, slash autocomplete."""
from __future__ import annotations

import os
from pathlib import Path

from textual import events
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.app import RenderResult
from rich.text import Text

from llm_code.tui.keybindings import KeybindingManager, load_keybindings

SLASH_COMMANDS = sorted([
    "/help", "/clear", "/exit", "/quit", "/model", "/cost", "/budget",
    "/undo", "/cd", "/config", "/thinking", "/vim", "/image", "/search",
    "/index", "/session", "/skill", "/plugin", "/mcp", "/memory",
    "/lsp", "/cancel", "/cron", "/task", "/swarm", "/voice", "/ide",
    "/vcr", "/hida", "/checkpoint", "/keybind", "/audit",
    "/plan", "/analyze", "/diff_check", "/dump", "/map",
    "/harness", "/knowledge",
])

# Commands that execute immediately (no arguments needed)
_NO_ARG_COMMANDS = frozenset({
    "/help", "/clear", "/cost", "/config", "/vim", "/skill", "/plugin",
    "/mcp", "/lsp", "/cancel", "/exit", "/quit", "/hida",
})

SLASH_COMMAND_DESCS: list[tuple[str, str]] = [
    ("/help", "Show help"),
    ("/clear", "Clear conversation"),
    ("/model", "Switch model"),
    ("/cost", "Token usage"),
    ("/budget", "Set token budget"),
    ("/undo", "Undo last change"),
    ("/cd", "Change directory"),
    ("/config", "Runtime config"),
    ("/thinking", "Toggle thinking"),
    ("/vim", "Toggle vim mode"),
    ("/image", "Attach image"),
    ("/search", "Search history"),
    ("/index", "Project index"),
    ("/session", "Sessions"),
    ("/skill", "Browse skills"),
    ("/plugin", "Browse plugins"),
    ("/mcp", "MCP servers"),
    ("/memory", "Project memory"),
    ("/cron", "Scheduled tasks"),
    ("/task", "Task lifecycle"),
    ("/swarm", "Swarm coordination"),
    ("/voice", "Voice input"),
    ("/ide", "IDE bridge"),
    ("/vcr", "VCR recording"),
    ("/checkpoint", "Checkpoints"),
    ("/hida", "HIDA classification"),
    ("/lsp", "LSP status"),
    ("/cancel", "Cancel generation"),
    ("/exit", "Quit"),
    ("/quit", "Quit"),
    ("/keybind", "Rebind keys"),
    ("/audit", "Audit log"),
    ("/plan", "Plan/Act mode"),
    ("/analyze", "Code analysis"),
    ("/diff_check", "Diff analysis"),
    ("/dump", "Dump context"),
    ("/map", "Repo map"),
    ("/harness", "Harness controls"),
    ("/knowledge", "Knowledge base"),
]


class InputBar(Widget):
    """Bottom input bar: ❯ {text}"""

    can_focus = True

    PROMPT = "❯ "

    DEFAULT_CSS = """
    InputBar {
        dock: bottom;
        height: auto;
        min-height: 3;
        max-height: 8;
        padding: 0 1;
        background: $surface;
    }
    InputBar:focus {
        border-top: solid $accent;
    }
    """

    value: reactive[str] = reactive("")
    disabled: reactive[bool] = reactive(False)
    vim_mode: reactive[str] = reactive("")  # "" | "NORMAL" | "INSERT"
    pending_image_count: reactive[int] = reactive(0)

    _show_dropdown: bool = False
    _dropdown_items: list[tuple[str, str]] = []
    _dropdown_cursor: int = 0

    def __init__(self) -> None:
        super().__init__()
        self._vim_engine = None
        self._cursor = 0  # cursor position within self.value
        self._show_dropdown = False
        self._dropdown_items = []
        self._dropdown_cursor = 0
        self._keybindings = load_keybindings(Path.home() / ".llmcode" / "keybindings.json")

    class Submitted(Message):
        """Fired when user presses Enter."""
        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    class Cancelled(Message):
        """Fired when user presses Escape during generation."""
        pass

    def watch_vim_mode(self) -> None:
        if self.vim_mode:
            from llm_code.vim.engine import VimEngine
            if self._vim_engine is None:
                self._vim_engine = VimEngine(self.value)
        else:
            self._vim_engine = None
        self.refresh()

    # Pink color matching Claude Code's image indicator
    _IMAGE_STYLE = "bold #e05880"
    _IMAGE_MARKER = "\x00IMG\x00"  # sentinel in value text

    def insert_image_marker(self) -> None:
        """Insert an [image] marker at current cursor position."""
        self.value = self.value[:self._cursor] + self._IMAGE_MARKER + self.value[self._cursor:]
        self._cursor += len(self._IMAGE_MARKER)
        self.pending_image_count += 1

    def _update_dropdown(self) -> None:
        """Recompute dropdown items based on current value."""
        was_showing = self._show_dropdown
        if self.value.startswith("/") and " " not in self.value:
            query = self.value
            self._dropdown_items = [
                (cmd, desc) for cmd, desc in SLASH_COMMAND_DESCS if cmd.startswith(query)
            ]
            self._dropdown_cursor = min(self._dropdown_cursor, max(0, len(self._dropdown_items) - 1))
            self._show_dropdown = len(self._dropdown_items) > 0
        else:
            self._dropdown_items = []
            self._dropdown_cursor = 0
            self._show_dropdown = False
        # Trigger relayout when dropdown visibility or item count changes
        if self._show_dropdown != was_showing:
            self.refresh(layout=True)

    def render(self) -> RenderResult:
        text = Text()
        # Render dropdown above prompt when active
        if self._show_dropdown and self._dropdown_items:
            visible = self._dropdown_items[:8]
            for i, (cmd, desc) in enumerate(visible):
                if i == self._dropdown_cursor:
                    text.append(f"  > {cmd:<20s} {desc}\n", style="bold white on #3a3a5a")
                else:
                    text.append(f"    {cmd:<20s} {desc}\n", style="dim")
        if self.vim_mode == "NORMAL":
            text.append("[N] ", style="yellow bold")
        elif self.vim_mode == "INSERT":
            text.append("[I] ", style="green bold")
        # Leading image count (for images added before any text)
        if self.pending_image_count > 0 and self._IMAGE_MARKER not in self.value:
            n = self.pending_image_count
            label = f"{n} image{'s' if n > 1 else ''}"
            text.append(f"[{label}] ", style=self._IMAGE_STYLE)
        text.append(self.PROMPT, style="bold cyan")
        if self.disabled:
            text.append("generating…", style="dim italic")
        else:
            # Render value with cursor at _cursor position
            val = self.value
            cur = min(self._cursor, len(val))
            before = val[:cur]
            after = val[cur:]
            # Render before cursor
            self._render_with_markers(text, before)
            # Cursor block
            if after:
                # Show character at cursor with highlight
                if after.startswith(self._IMAGE_MARKER):
                    text.append("[image]", style=f"{self._IMAGE_STYLE} reverse")
                    after = after[len(self._IMAGE_MARKER):]
                else:
                    text.append(after[0], style="reverse")
                    after = after[1:]
                self._render_with_markers(text, after)
            else:
                text.append("█", style="dim")
        return text

    def _render_with_markers(self, text: Text, s: str) -> None:
        """Render string with [image] markers styled in pink."""
        parts = s.split(self._IMAGE_MARKER)
        for i, part in enumerate(parts):
            if i > 0:
                text.append("[image] ", style=self._IMAGE_STYLE)
            if part:
                text.append(part)

    def get_clean_value(self) -> str:
        """Return value with image markers stripped (for display in chat)."""
        return self.value.replace(self._IMAGE_MARKER, "").strip()

    def on_key(self, event: events.Key) -> None:
        if self.disabled:
            if event.key == "escape":
                self.post_message(self.Cancelled())
            return

        # Dropdown navigation (when dropdown is visible)
        if self._show_dropdown and self._dropdown_items:
            if event.key == "up":
                self._dropdown_cursor = (self._dropdown_cursor - 1) % min(len(self._dropdown_items), 8)
                self.refresh()
                event.prevent_default()
                event.stop()
                return
            elif event.key == "down":
                self._dropdown_cursor = (self._dropdown_cursor + 1) % min(len(self._dropdown_items), 8)
                self.refresh()
                event.prevent_default()
                event.stop()
                return
            elif event.key in ("enter", "tab"):
                selected_cmd = self._dropdown_items[self._dropdown_cursor][0]
                self._show_dropdown = False
                self._dropdown_items = []
                self._dropdown_cursor = 0
                if selected_cmd in _NO_ARG_COMMANDS:
                    # Execute immediately
                    self.value = selected_cmd
                    self._cursor = 0
                    self.post_message(self.Submitted(selected_cmd))
                    self.value = ""
                else:
                    # Fill and wait for argument
                    self.value = selected_cmd + " "
                    self._cursor = len(self.value)
                self.refresh()
                return
            elif event.key == "escape":
                self._show_dropdown = False
                self._dropdown_items = []
                self._dropdown_cursor = 0
                self.refresh()
                return

        # Tab autocomplete (before vim routing) — fallback when dropdown not shown
        if event.key == "tab" and self.value.startswith("/"):
            matches = [c for c in SLASH_COMMANDS if c.startswith(self.value)]
            if len(matches) == 1:
                self.value = matches[0] + " "
                self._cursor = len(self.value)
            elif matches:
                prefix = os.path.commonprefix(matches)
                if len(prefix) > len(self.value):
                    self.value = prefix
                    self._cursor = len(self.value)
            return

        # Vim mode routing
        if self._vim_engine is not None:
            from llm_code.vim.types import VimMode
            key_str = event.key if len(event.key) > 1 else (event.character or event.key)
            self._vim_engine.feed_key(key_str)
            self.value = self._vim_engine.buffer
            # Update mode display
            self.vim_mode = "NORMAL" if self._vim_engine.mode == VimMode.NORMAL else "INSERT"
            # Handle enter in insert mode for submission
            if event.key == "enter" and self._vim_engine.mode == VimMode.INSERT:
                if self.value.strip():
                    self.post_message(self.Submitted(self.value))
                    self.value = ""
                    self._vim_engine.set_buffer("")
            return

        # Normal (non-vim) key handling — table lookup
        chord_action = self._keybindings.chord_state.feed(event.key)
        if chord_action is not None:
            self._handle_action(chord_action)
            return
        if self._keybindings.chord_state.pending is not None:
            return

        action = self._keybindings.get_action(event.key)
        if action:
            self._handle_action(action)
        elif event.character and len(event.character) == 1:
            self.value = self.value[:self._cursor] + event.character + self.value[self._cursor:]
            self._cursor += 1
            event.prevent_default()
            event.stop()

    def _handle_action(self, action: str) -> None:
        """Execute a named keybinding action."""
        if action == "submit":
            if self.value.strip():
                self.post_message(self.Submitted(self.value))
                self.value = ""
                self._cursor = 0
        elif action == "newline":
            self.value = self.value[:self._cursor] + "\n" + self.value[self._cursor:]
            self._cursor += 1
        elif action == "delete_back":
            if self._cursor > 0:
                self.value = self.value[:self._cursor - 1] + self.value[self._cursor:]
                self._cursor -= 1
        elif action == "delete_forward":
            if self._cursor < len(self.value):
                self.value = self.value[:self._cursor] + self.value[self._cursor + 1:]
        elif action == "cursor_left":
            if self._cursor > 0:
                self._cursor -= 1
                self.refresh()
        elif action == "cursor_right":
            if self._cursor < len(self.value):
                self._cursor += 1
                self.refresh()
        elif action == "cursor_home":
            self._cursor = 0
            self.refresh()
        elif action == "cursor_end":
            self._cursor = len(self.value)
            self.refresh()
        elif action == "cancel":
            self.value = ""
            self._cursor = 0
            self.post_message(self.Cancelled())

    def watch_value(self) -> None:
        # Keep cursor in bounds
        if self._cursor > len(self.value):
            self._cursor = len(self.value)
        self._update_dropdown()
        self.refresh()
