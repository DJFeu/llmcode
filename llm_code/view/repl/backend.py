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

import asyncio
from typing import Any, Dict, Optional, Sequence, TypeVar

from rich.console import Console

from llm_code.view.base import InputHandler, ViewBackend
from llm_code.view.dialog_types import Choice, TextValidator
from llm_code.view.repl.components.live_response_region import LiveResponseRegion
from llm_code.view.repl.components.tool_event_renderer import ToolEventRegion
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

        # M9: voice recorder state. Lazily initialized on first Ctrl+G.
        # ``_loop`` is captured during ``start()`` so recorder background
        # threads can safely schedule coordinator updates via
        # ``call_soon_threadsafe`` — this is the critical R3 (voice +
        # asyncio deadlock) mitigation from spec section 10.1.
        self._recorder: Any = None
        self._voice_active: bool = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    @property
    def coordinator(self) -> ScreenCoordinator:
        """Exposed for tests and component wiring. Production code
        outside view/repl/ should NOT use this — use Protocol methods."""
        return self._coordinator

    # === Lifecycle ===

    async def start(self) -> None:
        # Capture the main event loop on the main thread so background
        # threads (the recorder's audio callback) can schedule work via
        # ``loop.call_soon_threadsafe``. ``asyncio.get_running_loop()``
        # only works on the main thread — doing this here, not in the
        # background thread, is the R3 deadlock mitigation.
        self._loop = asyncio.get_running_loop()
        # Install voice toggle BEFORE coordinator.start() so the Ctrl+G
        # binding is baked into the PT Application on construction.
        self._coordinator.set_voice_toggle_callback(self._toggle_voice)
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
        return ToolEventRegion(
            console=self._coordinator._console,
            tool_name=tool_name,
            args=args,
        )

    def update_status(self, status: StatusUpdate) -> None:
        self._coordinator.update_status(status)

    # === Dialogs (delegated to coordinator.dialog_popover) ===

    async def show_confirm(
        self,
        prompt: str,
        default: bool = False,
        risk: RiskLevel = RiskLevel.NORMAL,
    ) -> bool:
        return await self._coordinator.dialog_popover.show_confirm(
            prompt, default=default, risk=risk,
        )

    async def show_select(
        self,
        prompt: str,
        choices: Sequence[Choice[T]],
        default: Optional[T] = None,
    ) -> T:
        return await self._coordinator.dialog_popover.show_select(
            prompt, choices, default=default,
        )

    async def show_text_input(
        self,
        prompt: str,
        default: Optional[str] = None,
        validator: Optional[TextValidator] = None,
        secret: bool = False,
    ) -> str:
        return await self._coordinator.dialog_popover.show_text_input(
            prompt, default=default, validator=validator, secret=secret,
        )

    async def show_checklist(
        self,
        prompt: str,
        choices: Sequence[Choice[T]],
        defaults: Optional[Sequence[T]] = None,
    ) -> Sequence[T]:
        return await self._coordinator.dialog_popover.show_checklist(
            prompt, choices, defaults=defaults,
        )

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

    # === Voice recording (M9) ===

    def voice_started(self) -> None:
        """Override the default no-op to flip the coordinator status."""
        self._coordinator.voice_started()

    def voice_progress(self, seconds: float, peak: float) -> None:
        self._coordinator.voice_progress(seconds, peak)

    def voice_stopped(self, reason: str) -> None:
        self._coordinator.voice_stopped(reason)

    def _toggle_voice(self) -> None:
        """Ctrl+G handler. Runs synchronously on the PT event loop."""
        if self._voice_active:
            self._stop_voice()
        else:
            self._start_voice()

    def _start_voice(self) -> None:
        """Begin recording. Lazily constructs the recorder on first use.

        Note: the M9 backend glue assumes a callback-style recorder
        API (``on_chunk_progress`` / ``on_auto_stop``). The real
        ``llm_code.tools.voice.AudioRecorder`` currently uses a polling
        interface, so this path falls through the try/except and prints
        an error in production. Tests monkeypatch ``AudioRecorder``
        with a callback-compatible FakeRecorder. A proper polling
        adapter lands in M10/M11 when runtime wiring is revisited.
        """
        if self._recorder is None:
            try:
                from llm_code.tools import voice as voice_module
                self._recorder = voice_module.AudioRecorder(
                    on_chunk_progress=self._on_recorder_chunk,
                    on_auto_stop=self._on_recorder_auto_stop,
                )
            except Exception as exc:  # noqa: BLE001
                self._coordinator.print_error_sync(
                    f"voice unavailable: {exc}"
                )
                return
        try:
            self._recorder.start()
            self._voice_active = True
            self.voice_started()
        except Exception as exc:  # noqa: BLE001
            self._coordinator.print_error_sync(
                f"voice start failed: {exc}"
            )

    def _stop_voice(self) -> None:
        """Manual Ctrl+G stop during recording."""
        if self._recorder is None or not self._voice_active:
            return
        try:
            self._recorder.stop()
        except Exception:  # noqa: BLE001
            pass
        self._voice_active = False
        self.voice_stopped(reason="manual_stop")
        asyncio.create_task(self._transcribe_and_insert())

    def _on_recorder_chunk(self, seconds: float, peak: float) -> None:
        """Called by the recorder on its background thread for each audio chunk.

        Forwards to the main loop via ``call_soon_threadsafe`` so the
        coordinator only mutates its state from one thread.
        """
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self.voice_progress, seconds, peak)

    def _on_recorder_auto_stop(self, reason: str) -> None:
        """Called by the recorder on VAD auto-stop (background thread)."""
        if self._loop is None:
            return
        self._voice_active = False
        self._loop.call_soon_threadsafe(self.voice_stopped, reason)
        self._loop.call_soon_threadsafe(
            lambda: asyncio.create_task(self._transcribe_and_insert())
        )

    async def _transcribe_and_insert(self) -> None:
        """After stop, transcribe captured audio and insert into input buffer."""
        if self._recorder is None:
            return
        try:
            text = await self._recorder.transcribe()
        except Exception as exc:  # noqa: BLE001
            self._coordinator.print_error_sync(
                f"transcription failed: {exc}"
            )
            return
        if text:
            self._coordinator._input_area.buffer.insert_text(text)
            if self._coordinator._app is not None:
                self._coordinator._app.invalidate()

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
