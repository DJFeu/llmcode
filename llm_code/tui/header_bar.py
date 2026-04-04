"""HeaderBar — single-line top bar showing model, project, branch."""
from __future__ import annotations

from textual.reactive import reactive
from textual.widget import Widget
from textual.app import RenderResult


class HeaderBar(Widget):
    """Single-line header: llm-code · {model} · {project} · {branch}"""

    model: reactive[str] = reactive("")
    project: reactive[str] = reactive("")
    branch: reactive[str] = reactive("")

    DEFAULT_CSS = """
    HeaderBar {
        dock: top;
        height: 1;
        background: $surface-darken-1;
        color: $text-muted;
        padding: 0 1;
    }
    """

    def _format_content(self) -> str:
        parts = ["llm-code"]
        if self.model:
            parts.append(self.model)
        if self.project:
            parts.append(self.project)
        if self.branch:
            parts.append(self.branch)
        return " · ".join(parts)

    def render(self) -> RenderResult:
        return self._format_content()

    def watch_model(self) -> None:
        self.refresh()

    def watch_project(self) -> None:
        self.refresh()

    def watch_branch(self) -> None:
        self.refresh()
