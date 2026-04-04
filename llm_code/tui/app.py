# llm_code/tui/app.py
"""LLMCodeTUI — Textual fullscreen app composing all widgets."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult

from llm_code.tui.chat_view import ChatScrollView, UserMessage, AssistantText
from llm_code.tui.header_bar import HeaderBar
from llm_code.tui.input_bar import InputBar
from llm_code.tui.status_bar import StatusBar
from llm_code.tui.theme import APP_CSS


class LLMCodeTUI(App):
    """Fullscreen TUI matching Claude Code's visual experience."""

    TITLE = "llm-code"
    CSS = APP_CSS
    ENABLE_MOUSE_SUPPORT = False  # CRITICAL: allow terminal mouse selection + copy

    def __init__(
        self,
        config: Any = None,
        cwd: Path | None = None,
        budget: int | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._cwd = cwd or Path.cwd()
        self._budget = budget
        self._runtime = None
        self._cost_tracker = None
        self._input_tokens = 0
        self._output_tokens = 0

    def compose(self) -> ComposeResult:
        yield HeaderBar(id="header-bar")
        yield ChatScrollView()
        yield InputBar()
        yield StatusBar(id="status-bar")

    def on_mount(self) -> None:
        header = self.query_one(HeaderBar)
        if self._config:
            header.model = getattr(self._config, "model", "")
        header.project = self._cwd.name
        header.branch = self._detect_branch()

    def _detect_branch(self) -> str:
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=self._cwd, capture_output=True, text=True, timeout=3,
            )
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""

    def on_input_bar_submitted(self, event: InputBar.Submitted) -> None:
        """Handle user input submission."""
        text = event.value.strip()
        if not text:
            return

        chat = self.query_one(ChatScrollView)
        chat.add_entry(UserMessage(text))

        if text.startswith("/"):
            self._handle_slash_command(text)
        else:
            self.run_worker(self._run_turn(text), name="run_turn")

    def on_input_bar_cancelled(self, event: InputBar.Cancelled) -> None:
        """Handle Escape — cancel running generation."""
        pass  # Phase 2: cancel runtime

    async def _run_turn(self, user_input: str) -> None:
        """Run a conversation turn — Phase 2 will wire ConversationRuntime."""
        chat = self.query_one(ChatScrollView)
        chat.add_entry(AssistantText("(runtime not connected yet)"))

    def _handle_slash_command(self, text: str) -> None:
        """Handle slash commands — Phase 7 will add full support."""
        chat = self.query_one(ChatScrollView)
        if text.strip() in ("/exit", "/quit"):
            self.exit()
        elif text.strip() == "/help":
            chat.add_entry(AssistantText("Available: /help /exit /quit /model /clear"))
        elif text.strip() == "/clear":
            chat.remove_children()
        else:
            chat.add_entry(AssistantText(f"Unknown command: {text}"))
