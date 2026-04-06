"""StatusBar — persistent bottom line with model, tokens, cost, hints."""
from __future__ import annotations

from textual.reactive import reactive
from textual.widget import Widget
from textual.app import RenderResult


class StatusBar(Widget):
    """Bottom status: model │ ↓tokens tok │ $cost │ streaming… │ /help │ Ctrl+D quit"""

    model: reactive[str] = reactive("")
    tokens: reactive[int] = reactive(0)
    cost: reactive[str] = reactive("")
    is_streaming: reactive[bool] = reactive(False)
    vim_mode: reactive[str] = reactive("")  # "" | "NORMAL" | "INSERT"
    is_local: reactive[bool] = reactive(False)
    plan_mode: reactive[str] = reactive("")  # "" | "PLAN"
    bg_tasks: reactive[int] = reactive(0)   # running/pending background tasks

    DEFAULT_CSS = """
    StatusBar {
        dock: bottom;
        height: 1;
        background: $surface-darken-1;
        color: $text-muted;
        padding: 0 1;
    }
    """

    def _format_content(self) -> str:
        parts: list[str] = []
        if self.plan_mode:
            parts.append(self.plan_mode)
        if self.vim_mode:
            parts.append(f"-- {self.vim_mode} --")
        if self.model:
            parts.append(self.model)
        if self.tokens > 0:
            parts.append(f"↓{self.tokens:,} tok")
        if self.is_local:
            parts.append("free")
        elif self.cost:
            parts.append(self.cost)
        if self.bg_tasks > 0:
            parts.append(f"{self.bg_tasks} task{'s' if self.bg_tasks > 1 else ''} running")
        if self.is_streaming:
            parts.append("streaming…")
        parts.append("/help")
        parts.append("Ctrl+D quit")
        return " │ ".join(parts)

    def render(self) -> RenderResult:
        return self._format_content()

    def watch_model(self) -> None:
        self.refresh()

    def watch_tokens(self) -> None:
        self.refresh()

    def watch_cost(self) -> None:
        self.refresh()

    def watch_is_streaming(self) -> None:
        self.refresh()

    def watch_vim_mode(self) -> None:
        self.refresh()

    def watch_is_local(self) -> None:
        self.refresh()

    def watch_plan_mode(self) -> None:
        self.refresh()

    def watch_bg_tasks(self) -> None:
        self.refresh()
