"""REPLBackend — v2.0.0 REPL implementation of ViewBackend.

Delegates all display work to ScreenCoordinator. The backend itself
is thin: it wires Protocol methods to coordinator methods, manages
handle objects for streaming/tool events, and holds config/runtime
references.

M3 ships the skeleton (coordinator + empty layout). M4-M9 add the
components (status, input, popover, live response, tool events,
dialogs, voice).
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Sequence, TypeVar

from rich.console import Console

from llm_code.view.base import InputHandler, ViewBackend
from llm_code.view.dialog_types import Choice, DialogCancelled, TextValidator
from llm_code.view.repl.components.live_response_region import LiveResponseRegion
from llm_code.view.repl.coordinator import ScreenCoordinator
from llm_code.view.types import (
    MessageEvent,
    Role,
    RiskLevel,
    StatusUpdate,
    StreamingMessageHandle,
    ToolEventHandle,
)

T = TypeVar("T")


class _NullToolEventHandle:
    """M3 placeholder. Real implementation in M7 (ToolEventRegion)."""

    def __init__(
        self,
        coordinator: ScreenCoordinator,
        tool_name: str,
        args: Dict[str, Any],
    ) -> None:
        self._coordinator = coordinator
        self._tool_name = tool_name
        self._args = args
        self._committed = False

        # Print start line immediately
        self._coordinator._console.print(
            f"[dim]▶[/dim] {tool_name}"
        )

    def feed_stdout(self, line: str) -> None:
        pass

    def feed_stderr(self, line: str) -> None:
        pass

    def feed_diff(self, diff_text: str) -> None:
        pass

    def commit_success(
        self,
        *,
        summary: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if self._committed:
            return
        self._committed = True
        summary_text = summary or "done"
        self._coordinator._console.print(
            f"[green]✓[/green] {self._tool_name} · {summary_text}"
        )

    def commit_failure(
        self,
        *,
        error: str,
        exit_code: Optional[int] = None,
    ) -> None:
        if self._committed:
            return
        self._committed = True
        exit_str = f" · exit {exit_code}" if exit_code is not None else ""
        self._coordinator._console.print(
            f"[red]✗[/red] {self._tool_name} · {error}{exit_str}"
        )

    @property
    def is_active(self) -> bool:
        return not self._committed


class REPLBackend(ViewBackend):
    """REPL ViewBackend — prompt_toolkit + Rich implementation.

    All display concerns delegate to ``self._coordinator``. The backend
    itself exists to implement the ViewBackend ABC and hold references
    to config/runtime for future use.

    M3 scope: coordinator skeleton, null-style handles for streaming
    and tool events. M6/M7 replace the null handles with real ones.
    """

    def __init__(
        self,
        *,
        config: Any = None,
        runtime: Any = None,
        console: Optional[Console] = None,
    ) -> None:
        self._config = config
        self._runtime = runtime
        self._coordinator = ScreenCoordinator(console=console)
        # Tracks the currently-active streaming region (M6). Starting a
        # new stream while one is already active aborts the previous —
        # a defensive guard against dispatcher bugs; normal flow always
        # commits or aborts before starting the next turn.
        self._active_streaming_region: Optional[LiveResponseRegion] = None

    @property
    def coordinator(self) -> ScreenCoordinator:
        """Exposed for tests and component wiring. Production code
        outside view/repl/ should NOT use this — use Protocol methods."""
        return self._coordinator

    # === Lifecycle ===

    async def start(self) -> None:
        await self._coordinator.start()

    async def stop(self) -> None:
        await self._coordinator.stop()

    async def run(self) -> None:
        await self._coordinator.run()

    def mark_fatal_error(
        self,
        code: str,
        message: str,
        retryable: bool = True,
    ) -> None:
        self._coordinator.print_error_sync(
            f"[{code}] {message} (retryable={retryable})"
        )

    # === Input ===

    def set_input_handler(self, handler: InputHandler) -> None:
        self._coordinator.set_input_callback(handler)

    # === Messages ===

    def render_message(self, event: MessageEvent) -> None:
        self._coordinator.render_message_sync(event)

    def start_streaming_message(
        self,
        role: Role,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> StreamingMessageHandle:
        # Abort any still-active previous region (shouldn't happen in
        # normal flow but protects against dispatcher bugs).
        if (
            self._active_streaming_region is not None
            and self._active_streaming_region.is_active
        ):
            self._active_streaming_region.abort()

        region = LiveResponseRegion(
            console=self._coordinator._console,
            coordinator=self._coordinator,
            role=role,
        )
        region.start()
        self._active_streaming_region = region
        return region

    def start_tool_event(
        self,
        tool_name: str,
        args: Dict[str, Any],
    ) -> ToolEventHandle:
        return _NullToolEventHandle(self._coordinator, tool_name, args)

    def update_status(self, status: StatusUpdate) -> None:
        self._coordinator.update_status(status)

    # === Dialogs (M3 placeholder: always return default; M8 replaces) ===

    async def show_confirm(
        self,
        prompt: str,
        default: bool = False,
        risk: RiskLevel = RiskLevel.NORMAL,
    ) -> bool:
        self._coordinator.print_info_sync(f"[confirm] {prompt} (auto: {default})")
        return default

    async def show_select(
        self,
        prompt: str,
        choices: Sequence[Choice[T]],
        default: Optional[T] = None,
    ) -> T:
        self._coordinator.print_info_sync(f"[select] {prompt}")
        if default is not None:
            return default
        if choices:
            return choices[0].value
        raise DialogCancelled("no choices available")

    async def show_text_input(
        self,
        prompt: str,
        default: Optional[str] = None,
        validator: Optional[TextValidator] = None,
        secret: bool = False,
    ) -> str:
        self._coordinator.print_info_sync(f"[text] {prompt}")
        return default or ""

    async def show_checklist(
        self,
        prompt: str,
        choices: Sequence[Choice[T]],
        defaults: Optional[Sequence[T]] = None,
    ) -> Sequence[T]:
        self._coordinator.print_info_sync(f"[checklist] {prompt}")
        return list(defaults) if defaults else []

    # === Convenience output ===

    def print_info(self, text: str) -> None:
        self._coordinator.print_info_sync(text)

    def print_warning(self, text: str) -> None:
        self._coordinator.print_warning_sync(text)

    def print_error(self, text: str) -> None:
        self._coordinator.print_error_sync(text)

    def print_panel(self, content: str, title: Optional[str] = None) -> None:
        self._coordinator.print_panel_sync(content, title)

    def clear_screen(self) -> None:
        self._coordinator.clear_screen_sync()

    # === External editor (M3 placeholder; real impl via $EDITOR in M9 or later) ===

    async def open_external_editor(
        self,
        initial_text: str = "",
        filename_hint: str = ".md",
    ) -> str:
        self._coordinator.print_info_sync(
            "[editor] external editor not implemented yet (M3 placeholder)"
        )
        return initial_text
