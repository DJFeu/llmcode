"""ScreenCoordinator — single owner of prompt_toolkit Application + Rich Console.

The coordinator is the only class in the REPL backend that talks directly
to the terminal. Every other component (StatusLine in M5, InputArea in M4,
LiveResponseRegion in M6, ToolEventRegion in M7, DialogPopover in M8,
VoiceOverlay in M9) delegates its display work back through the coordinator.

This single-owner invariant is the architectural response to the v1.x TUI
bug class (see spec section 1.1 and 10.1 R1). With one lock and one
Application, there's exactly one place where screen-corruption bugs can
originate, and exactly one place to fix them.

M3 ships the skeleton: lifecycle (start/stop/run), empty layout (1-line
reverse-video status placeholder + 3-line empty input area), Ctrl+D exit,
and terminal-native scrollback for everything above. M4-M9 plug real
components into the layout slots.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.styles import Style
from rich.console import Console

from llm_code.view.types import (
    MessageEvent,
    StatusUpdate,
)


InputCallback = Callable[[str], Awaitable[None]]


class ScreenCoordinator:
    """Owns the prompt_toolkit Application and rich.Console for a REPL session.

    Invariants:

    1. Exactly one Application exists per coordinator instance.
    2. Exactly one Console (writing to the real stdout, not a buffer) exists.
    3. All direct writes to stdout go through the console (no bare print()).
    4. PT Application.invalidate() and console.print() never run concurrently
       — the ``_screen_lock`` asyncio.Lock arbitrates.
    5. The coordinator's ``run()`` is the main event loop; ``stop()`` sets
       ``_exit_requested`` and lets run() return cleanly.

    Component plug-in points (used in M4-M9):

    - ``_status_text_fn`` — callable returning the status line string.
      M5 replaces the placeholder with a StatusLine component.
    - ``_input_buffer`` — the prompt_toolkit Buffer for user typing.
      M4 swaps the barebones Buffer for an InputArea component with
      multi-line, slash-popover, and keybinding integration.
    - ``_key_bindings`` — the PT KeyBindings. M4 adds Enter/Shift+Enter/
      Ctrl+G/history/etc. M3 only wires Ctrl+D -> exit.
    - ``_extra_layout_rows`` — optional HSplit children inserted above
      the input area. M6/M7 use this for ConditionalContainer-wrapped
      voice overlays, rate limit warnings, etc.
    """

    def __init__(
        self,
        *,
        console: Optional[Console] = None,
    ) -> None:
        # Console is the real terminal by default. Tests override with
        # Console(file=io.StringIO(), force_terminal=True) for output
        # capture.
        self._console = console or Console()

        # asyncio primitives
        self._screen_lock = asyncio.Lock()
        self._exit_event = asyncio.Event()
        self._exit_requested = False

        # prompt_toolkit state — constructed in start()
        self._app: Optional[Application] = None
        self._input_buffer: Buffer = Buffer(multiline=True)
        self._key_bindings = KeyBindings()

        # Input callback installed by backend.set_input_handler()
        self._input_callback: Optional[InputCallback] = None

        # Status state — M5 expands this into StatusLine component
        self._current_status = StatusUpdate()

        # Pre-register Ctrl+D to exit. M4 adds Enter/Shift+Enter/etc.
        @self._key_bindings.add("c-d")
        def _exit(event: Any) -> None:
            # Ctrl+D: exit on empty input buffer, delete-char otherwise
            if not self._input_buffer.text:
                self.request_exit()
                event.app.exit()

        # Ctrl+C: clear input, or exit if already empty
        @self._key_bindings.add("c-c")
        def _interrupt(event: Any) -> None:
            if self._input_buffer.text:
                self._input_buffer.reset()
            else:
                self.request_exit()
                event.app.exit()

        # Enter: submit current buffer to the input callback
        @self._key_bindings.add("enter")
        def _submit(event: Any) -> None:
            text = self._input_buffer.text.strip()
            if not text:
                return
            self._input_buffer.reset()
            if self._input_callback is not None:
                asyncio.create_task(self._invoke_callback(text))
            event.app.invalidate()

    async def _invoke_callback(self, text: str) -> None:
        """Wrap input_callback invocation so exceptions don't kill the
        event loop. Errors are printed via print_error_sync()."""
        try:
            assert self._input_callback is not None
            await self._input_callback(text)
        except Exception as exc:  # noqa: BLE001 — we want any exception
            self.print_error_sync(f"input handler failed: {exc}")
        finally:
            if self._app is not None and self._app.is_running:
                self._app.invalidate()

    # === Lifecycle ===

    async def start(self) -> None:
        """Construct the PT Application. Idempotent — safe to call twice,
        subsequent calls are no-ops."""
        if self._app is not None:
            return

        layout = self._build_layout()
        style = self._build_style()

        self._app = Application(
            layout=layout,
            key_bindings=self._key_bindings,
            full_screen=False,     # KEY: no alt-screen, scrollback stays native
            mouse_support=False,   # KEY: no mouse capture, native selection works
            style=style,
        )

    async def stop(self) -> None:
        """Tear down the PT Application. Idempotent."""
        if self._app is not None and self._app.is_running:
            self._app.exit()
        self._app = None

    async def run(self) -> None:
        """Main event loop. Blocks until the user requests exit."""
        if self._app is None:
            await self.start()
        assert self._app is not None

        try:
            await self._app.run_async()
        except (EOFError, KeyboardInterrupt):
            pass

        self._exit_event.set()

    def request_exit(self) -> None:
        """Signal the main loop to exit at the next iteration."""
        self._exit_requested = True
        self._exit_event.set()

    @property
    def is_running(self) -> bool:
        return self._app is not None and self._app.is_running

    # === Input handler wiring ===

    def set_input_callback(self, callback: InputCallback) -> None:
        """Install the async handler invoked on each submitted input."""
        self._input_callback = callback

    # === Layout construction ===

    def _build_layout(self) -> Layout:
        """Build the bottom layout: placeholder status line + empty input area.

        Components in M4+ replace these placeholders via the coordinator's
        layout swap API (to be designed when M4 needs it). For now, the
        layout is static with placeholder content.
        """
        status_window = Window(
            FormattedTextControl(self._status_text),
            height=1,
            style="class:status",
        )
        input_window = Window(
            BufferControl(buffer=self._input_buffer),
            height=3,
            style="class:input",
        )
        return Layout(HSplit([status_window, input_window]))

    def _status_text(self) -> str:
        """Current status line as a plain string.

        M5 replaces this with a formatted-text function that renders
        model/cost/tokens inline. For M3, it's an empty placeholder
        to verify layout wiring works.
        """
        return " llmcode REPL — M3 skeleton "

    def _build_style(self) -> Style:
        return Style.from_dict({
            "status": "reverse",
            "input": "",
        })

    # === Output methods delegated by REPLBackend ===
    # Each must acquire _screen_lock before writing to the console, so
    # PT redraws and our writes don't interleave.

    async def acquire_screen(self):
        """Async accessor: returns the ``_screen_lock`` for safe stdout writes.

        Usage:
            lock = await self._coordinator.acquire_screen()
            async with lock:
                self._coordinator._console.print("...")
        """
        return self._screen_lock

    def render_message_sync(self, event: MessageEvent) -> None:
        """Print a user-echo / system-note message to scrollback.

        Synchronous version — safe when called from the PT key-binding
        dispatcher (which doesn't yield to the event loop). For async
        contexts, prefer render_message_async.
        """
        prefix_map = {
            "user": "[bold green]>[/bold green] ",
            "assistant": "[bold cyan]<[/bold cyan] ",
            "system": "[dim]·[/dim] ",
            "tool": "[dim]▸[/dim] ",
        }
        prefix = prefix_map.get(event.role.value, "")
        self._console.print(f"{prefix}{event.content}")

    async def render_message_async(self, event: MessageEvent) -> None:
        async with self._screen_lock:
            self.render_message_sync(event)

    def print_info_sync(self, text: str) -> None:
        self._console.print(f"[blue]ℹ[/blue] {text}")

    def print_warning_sync(self, text: str) -> None:
        self._console.print(f"[yellow]⚠[/yellow] {text}")

    def print_error_sync(self, text: str) -> None:
        self._console.print(f"[red]✗[/red] {text}")

    def print_panel_sync(self, content: str, title: Optional[str] = None) -> None:
        from rich.panel import Panel
        self._console.print(Panel(content, title=title, border_style="cyan"))

    def clear_screen_sync(self) -> None:
        """Clear the terminal. Uses Rich's console.clear() which
        respects the console's file (no-op on captured consoles)."""
        self._console.clear()

    # === Status ===

    def update_status(self, status: StatusUpdate) -> None:
        """Merge a partial StatusUpdate into the current state.

        M5 expands this to actually refresh the status line; for M3 we
        just store it so tests can assert on the merged state.
        """
        for field_name in status.__dataclass_fields__:
            value = getattr(status, field_name)
            # Merge rule: None = unchanged; False = unchanged (default);
            # non-None non-False = overwrite
            if value is None:
                continue
            if field_name == "is_streaming" and value is False:
                # False on is_streaming is a meaningful clear, always apply
                pass
            setattr(self._current_status, field_name, value)

        if self._app is not None and self._app.is_running:
            self._app.invalidate()

    @property
    def current_status(self) -> StatusUpdate:
        return self._current_status
