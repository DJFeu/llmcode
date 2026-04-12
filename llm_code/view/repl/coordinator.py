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
from typing import Awaitable, Callable, Optional

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import merge_key_bindings
from prompt_toolkit.layout import FloatContainer, HSplit, Layout, Window
from prompt_toolkit.layout.containers import ConditionalContainer
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from rich.console import Console

from llm_code.view.repl.components.dialog_popover import (
    DialogPopover,
    build_dialog_float,
    build_dialog_key_bindings,
)
from llm_code.view.repl.components.footer_hint import FooterHint
from llm_code.view.repl.components.inline_select import (
    InlineSelectState,
    SelectionChoice,
    build_inline_select_keybindings,
)
from llm_code.view.repl.components.input_area import InputArea
from llm_code.view.repl.components.mode_indicator import ModeIndicator
from llm_code.view.repl.components.status_line import StatusLine
from llm_code.view.repl.components.voice_overlay import VoiceOverlay
from llm_code.view.repl.history import PromptHistory, default_history_path
from llm_code.view.repl.keybindings import build_keybindings
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
    4. Rich and prompt_toolkit coexist via their own internal locking:
       Rich's Live has an internal lock, PT's ``invalidate()`` is
       event-loop-thread-safe, and the M0 PoC plus the full M0-M9 test
       suite showed no contention on Warp/iTerm2/tmux. A dedicated
       ``_screen_lock`` was introduced in M3 as an R1 mitigation but
       never acquired by any production code path — dropped in M9.5
       (YAGNI). If a real race surfaces in future work, re-add with
       a specific reproducer.
    5. The coordinator's ``run()`` is the main event loop; ``stop()`` sets
       ``_exit_requested`` and lets run() return cleanly.

    Component plug-in points (used in M4-M9):

    - ``_status_text`` — callable returning the status line string.
      M5 replaces the placeholder with a StatusLine component.
    - ``_input_area`` — the InputArea component owning the PT Buffer,
      slash completer, popover Float, and dynamic window height. Added
      in M4.
    - ``_key_bindings`` — produced by ``build_keybindings()`` in
      ``view/repl/keybindings.py``. M4 wires Enter/Ctrl+D/Ctrl+C/
      Ctrl+J/Alt+Enter/Ctrl+U/Ctrl+Up/Ctrl+Down. M9 adds the voice
      hotkey via the ``on_voice_toggle`` parameter.
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
        self._exit_event = asyncio.Event()
        self._exit_requested = False

        # prompt_toolkit state — Application constructed in start()
        self._app: Optional[Application] = None

        # Input callback installed by backend.set_input_handler()
        self._input_callback: Optional[InputCallback] = None

        # M5: StatusLine owns merged status state + formatted rendering.
        # Replaces M3's raw StatusUpdate + inline format function.
        self._status_line = StatusLine()
        self._footer_hint = FooterHint()
        self._mode_indicator = ModeIndicator()

        # M4: input area + slash popover + history, plus the KeyBindings
        # factory from keybindings.py. Replaces the M3 inline closures.
        self._history = PromptHistory(path=default_history_path())
        # M15: inject history so the ghost text processor can preview
        # the latest entry on an empty buffer.
        self._input_area = InputArea(history=self._history)

        # M8: dialog popover hosts confirm/select/text/checklist overlays.
        self._dialog_popover = DialogPopover()

        # M15: inline selection list (replaces input area temporarily)
        self._inline_select: Optional[InlineSelectState] = None

        # M9: voice overlay flips the status line into recording mode.
        self._voice_overlay = VoiceOverlay(self)
        # Backend installs the Ctrl+G handler via set_voice_toggle_callback
        # before start() constructs the Application.
        self._voice_toggle_callback: Optional[Callable[[], None]] = None

        # Main input bindings + dialog bindings merged together. Dialog
        # bindings have Condition filters that only fire while a dialog
        # is active, so they don't interfere with normal input flow.
        self._rebuild_key_bindings()

    # M3 Buffer is now owned by InputArea; expose a pass-through property
    # so any external caller that historically used `coord._input_buffer`
    # keeps working during the transition.
    @property
    def input_buffer(self) -> Buffer:
        return self._input_area.buffer

    @property
    def dialog_popover(self) -> DialogPopover:
        """Exposed to REPLBackend for delegating show_confirm/select/etc."""
        return self._dialog_popover

    @property
    def voice_overlay(self) -> VoiceOverlay:
        """Exposed to REPLBackend for voice_* state queries."""
        return self._voice_overlay

    def _rebuild_key_bindings(self) -> None:
        """Reconstruct the merged key bindings.

        Called from __init__ and whenever the voice toggle callback
        changes. Must be invoked BEFORE ``start()`` — once the PT
        Application is constructed it captures the current key bindings
        and later rebuilds don't take effect.
        """
        self._key_bindings = merge_key_bindings([
            build_keybindings(
                input_buffer=self._input_area.buffer,
                history=self._history,
                on_submit=self._handle_submit,
                on_exit=self.request_exit,
                on_voice_toggle=self._voice_toggle_callback,
            ),
            build_dialog_key_bindings(self._dialog_popover),
            build_inline_select_keybindings(
                state_getter=lambda: self._inline_select,
                on_done=self._finish_inline_select,
            ),
        ])

    def set_voice_toggle_callback(
        self, callback: Optional[Callable[[], None]],
    ) -> None:
        """Install the Ctrl+G voice hotkey handler.

        Must be called BEFORE ``start()`` so the rebuilt key bindings
        are the ones the PT Application uses.
        """
        self._voice_toggle_callback = callback
        self._rebuild_key_bindings()

    # === Voice state forwarding (called by backend) ===

    def voice_started(self) -> None:
        self._voice_overlay.start()
        if self._app is not None and self._app.is_running:
            self._app.invalidate()

    def voice_progress(self, seconds: float, peak: float) -> None:
        self._voice_overlay.update(seconds, peak)
        if self._app is not None and self._app.is_running:
            self._app.invalidate()

    def voice_stopped(self, reason: str) -> None:
        self._voice_overlay.stop(reason)
        if self._app is not None and self._app.is_running:
            self._app.invalidate()

    def _handle_submit(self, text: str) -> None:
        """Called by the Enter keybinding with the submitted text.

        Records the entry in prompt history, then schedules the async
        input callback (if one is installed) so the dispatcher can
        process the turn.
        """
        self._history.add(text)
        if self._input_callback is not None:
            asyncio.create_task(self._invoke_callback(text))

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
        # M15: inject app reference into DialogPopover so it can
        # invalidate() when a dialog becomes active/inactive — needed
        # for /skill, /plugin, /mcp interactive browsers where no
        # other output triggers a natural PT redraw.
        self._dialog_popover.set_app(self._app)

    async def stop(self) -> None:
        """Tear down the PT Application. Idempotent."""
        if self._app is not None and self._app.is_running:
            self._app.exit()
        self._app = None

    async def run(self) -> None:
        """Main event loop. Blocks until the user requests exit.

        Wraps ``run_async`` in ``prompt_toolkit.patch_stdout`` so any
        ``print`` / Rich ``console.print`` / stdout write from a
        worker thread (streaming renderer, voice STT, background
        tools) flows through PT's output buffer. Without this, raw
        stdout writes interleave with PT's drawing commands — the
        status line + input area drift away from the terminal
        bottom, and scrolling breaks.

        This is the single biggest PT non-fullscreen mode idiom: any
        app that also writes to stdout outside PT's own draw loop
        MUST use ``patch_stdout`` or the layout will corrupt the
        moment a non-PT thread prints something.
        """
        from prompt_toolkit.patch_stdout import patch_stdout

        if self._app is None:
            await self.start()
        assert self._app is not None

        try:
            with patch_stdout(raw=True):
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
        """Build the REPL bottom layout.

        Structure:
            FloatContainer
            |- HSplit
            |  |- rate-limit warning (ConditionalContainer, hidden by default)
            |  |- status line (1 row, reverse video)
            |  |- InputArea window (1-12 rows, dynamic)
            |- Float: slash-completion popover (ConditionalContainer,
               only shown when input starts with '/')
        """
        rate_limit_warning = Window(
            FormattedTextControl(self._status_line.render_rate_limit_warning),
            height=1,
            style="class:rate-limit",
        )
        rate_limit_container = ConditionalContainer(
            content=rate_limit_warning,
            filter=Condition(self._status_line.is_rate_limited),
        )
        status_window = Window(
            FormattedTextControl(self._status_text),
            height=1,
            style="class:status",
        )
        input_window = self._input_area.build_window()
        popover_float = self._input_area.build_popover_float()
        dialog_float = build_dialog_float(self._dialog_popover)
        footer_window = Window(
            FormattedTextControl(self._footer_text),
            height=1,
            style="class:footer-hint",
        )
        footer_container = ConditionalContainer(
            content=footer_window,
            filter=~Condition(lambda: self._dialog_popover.is_active),
        )
        # M15: inline selection list replaces the input area when active
        inline_select_window = ConditionalContainer(
            content=Window(
                FormattedTextControl(self._inline_select_text),
                wrap_lines=True,
            ),
            filter=Condition(lambda: self._inline_select is not None),
        )
        input_container = ConditionalContainer(
            content=input_window,
            filter=Condition(lambda: self._inline_select is None),
        )
        # Spacer at the top of the HSplit: fills remaining terminal
        # height so the FloatContainer is as tall as the terminal.
        # This is critical for Floats (completion popover, dialog):
        # without a spacer the HSplit is only ~4 rows, and Floats
        # can't exceed their container. The spacer's content is
        # empty — it represents the "native scrollback" region that
        # patch_stdout's Rich output occupies above the PT chrome.
        def _spacer_height() -> int:
            import shutil
            try:
                rows = shutil.get_terminal_size((80, 24)).lines
            except Exception:
                rows = 24
            # Reserve: status(1) + input(1-12) + footer(1) + rate-limit(0-1)
            fixed = 4
            return max(0, rows - fixed)

        spacer = Window(height=_spacer_height)

        return Layout(
            FloatContainer(
                content=HSplit([
                    spacer,
                    rate_limit_container,
                    status_window,
                    inline_select_window,
                    input_container,
                    footer_container,
                ]),
                floats=[popover_float, dialog_float],
            )
        )

    def _inline_select_text(self) -> FormattedText:
        """Render the inline selection list. Called by PT on each redraw."""
        if self._inline_select is None:
            return FormattedText([])
        return self._inline_select.render()

    async def start_inline_select(
        self,
        prompt: str,
        choices: list[SelectionChoice],
    ):
        """Show an inline selection list and await the user's choice.

        The input area is hidden while the selection is active.
        Returns the selected choice's value, or None if cancelled.
        """
        import asyncio
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        state = InlineSelectState(prompt=prompt, choices=choices, future=future)
        # Set visible rows based on terminal height
        try:
            import shutil
            rows = shutil.get_terminal_size((80, 24)).lines
            state._visible_rows = max(5, rows - 5)
        except Exception:
            state._visible_rows = 18
        self._inline_select = state
        if self._app is not None:
            self._app.invalidate()
        try:
            return await future
        finally:
            self._inline_select = None
            if self._app is not None:
                self._app.invalidate()

    def _finish_inline_select(self) -> None:
        """Called by the inline-select keybinding after Enter/Esc.

        After clearing the inline select, re-push the cursor to the
        bottom of the terminal so the input area anchors at the
        bottom again. Without this, PT re-renders the now-small
        layout at the cursor position left by the tall selection
        list (near the top of the terminal).
        """
        self._inline_select = None
        self._re_anchor_bottom()
        if self._app is not None:
            self._app.invalidate()

    def _re_anchor_bottom(self) -> None:
        """Push the terminal cursor to the bottom of the viewport.

        Same technique as ``cli.main._push_cursor_to_bottom()`` but
        callable from within a running PT session. Writes newlines
        through the console so they flow through ``patch_stdout``.
        """
        import shutil
        import sys
        try:
            rows = shutil.get_terminal_size((80, 24)).lines
        except Exception:
            rows = 24
        # Layout = status(1) + input(1-12) + footer(1) = 3-14 rows.
        # Fill enough to push the cursor to the bottom.
        reserved = 5
        fill = max(0, rows - reserved)
        if fill > 0:
            sys.stdout.write("\n" * fill)
            sys.stdout.flush()

    def _footer_text(self) -> FormattedText:
        """Render the footer hint + mode indicator row."""
        hint = self._footer_hint.render()
        mode = self._mode_indicator.render()
        # Separator between hints and mode label.
        sep = [("", "   ")]
        return FormattedText(hint + sep + mode)

    def _status_text(self) -> FormattedText:
        """Render the status line. Called by FormattedTextControl on each
        PT redraw, so spinner advance and render happen in sync."""
        self._status_line.advance_spinner()
        return self._status_line.render_formatted_text()

    def _build_style(self) -> Style:
        return Style.from_dict({
            "status": "reverse",
            "status.mode": "reverse fg:ansiyellow",
            "status.spinner": "reverse fg:ansicyan",
            "rate-limit": "fg:ansired reverse",
            "input": "",
            # M8: dialog popover styles
            "dialog.frame": "fg:ansiwhite bg:ansiblack",
            "dialog.header": "fg:ansicyan bold",
            "dialog.selected": "reverse",
            "dialog.normal": "",
            "dialog.elevated": "fg:ansiyellow",
            "dialog.high": "fg:ansired bold",
            "dialog.critical": "fg:ansired reverse bold",
            "dialog.error": "fg:ansired",
        })

    # === Output methods delegated by REPLBackend ===
    # Rich's internal locking plus prompt_toolkit's event-loop-safe
    # invalidate() are sufficient for the M0-M9 workload — no explicit
    # arbitration happens here.

    def render_message_sync(self, event: MessageEvent) -> None:
        """Print a user-echo / system-note message to scrollback.

        Safe to call from the PT key-binding dispatcher (no awaiting),
        from background handlers marshalled via call_soon_threadsafe,
        and from async contexts alike — Rich's Console.print() is
        thread-safe.
        """
        prefix_map = {
            "user": "[bold green]>[/bold green] ",
            "assistant": "[bold cyan]<[/bold cyan] ",
            "system": "[dim]·[/dim] ",
            "tool": "[dim]▸[/dim] ",
        }
        prefix = prefix_map.get(event.role.value, "")
        self._console.print(f"{prefix}{event.content}")

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
        """Merge a partial StatusUpdate into the StatusLine component.

        Non-None fields overwrite current state; None fields are ignored.
        Boolean False on ``is_streaming`` and ``voice_active`` is treated
        as a meaningful clear (so the streaming spinner and voice mode
        can be turned off explicitly).
        """
        self._status_line.merge(status)
        if self._app is not None and self._app.is_running:
            self._app.invalidate()

    @property
    def current_status(self) -> StatusUpdate:
        return self._status_line.state
