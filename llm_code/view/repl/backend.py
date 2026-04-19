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
        self._coordinator.set_plan_toggle_callback(self._toggle_plan_mode)
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

    def request_exit(self) -> None:
        """Graceful exit — sets the coordinator's exit flag and wakes
        the PT Application. Safe to call multiple times.
        """
        self._coordinator.request_exit()
        app = self._coordinator._app
        if app is not None and app.is_running:
            app.exit()

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

    def _toggle_plan_mode(self) -> None:
        """Shift+Tab handler — flip between plan and build.

        Runs synchronously on the PT event loop. Routes through
        :meth:`PermissionPolicy.switch_to` so the ModeTransition
        event is recorded and the build-switch reminder auto-injects
        on the next system prompt.
        """
        if self._runtime is None:
            return
        policy = getattr(self._runtime, "_permissions", None)
        if policy is None or not hasattr(policy, "switch_to"):
            return
        from llm_code.runtime.permissions import PermissionMode

        plan_modes = {PermissionMode.PLAN, PermissionMode.READ_ONLY}
        current = policy.mode
        target = PermissionMode.WORKSPACE_WRITE if current in plan_modes else PermissionMode.PLAN
        policy.switch_to(target)
        # Mirror dispatcher's plan_mode flag so the status-line /
        # mode-indicator reflects the flip on next render.
        self._runtime.plan_mode = target is PermissionMode.PLAN
        label = "plan" if target is PermissionMode.PLAN else "build"
        self._coordinator.print_info_sync(f"Switched to {label} mode")

    def _start_voice(self) -> None:
        """Begin recording. Lazily constructs the recorder on first use.

        Wires the REPL's callback-style voice glue
        (``_on_recorder_chunk`` / ``_on_recorder_auto_stop``) to the
        polling ``AudioRecorder`` via :class:`PollingRecorderAdapter`
        (M9.5). The adapter constructs a real ``AudioRecorder``, spins
        a polling task on ``start()``, and fires callbacks from the
        main asyncio loop — no background-thread juggling needed here.

        Recorder construction and STT factory lookup are both wrapped
        in try/except so a missing optional voice dependency, a denied
        mic permission, or a bad STT config surfaces as an inline
        "voice unavailable" message instead of an unhandled exception.
        """
        if self._recorder is None:
            try:
                from llm_code.view.repl.recorder_adapter import (
                    PollingRecorderAdapter,
                )
                stt = self._build_stt_engine()
                cfg = self._voice_cfg()
                # Read silence_seconds + silence_threshold from config
                # so a user with a quieter mic can tune both without
                # editing source. Defaults match the config loader
                # (2.0s, 500) rather than the older AudioRecorder
                # defaults (0.0s, 3000) — the 3000 threshold was too
                # high for the average laptop mic and caused the VAD
                # auto-stop to silently never fire in v1.x.
                self._recorder = PollingRecorderAdapter(
                    on_chunk_progress=self._on_recorder_chunk,
                    on_auto_stop=self._on_recorder_auto_stop,
                    silence_seconds=float(
                        getattr(cfg, "silence_seconds", 2.0) or 2.0
                    ),
                    silence_threshold=int(
                        getattr(cfg, "silence_threshold", 500) or 500
                    ),
                    stt_engine=stt,
                    language=self._voice_language(),
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

    def _voice_cfg(self) -> Any:
        """Return the ``config.voice`` sub-config, or None.

        Centralizes the "cfg may be None" defensive reads so the
        call sites in ``_start_voice`` / ``_build_stt_engine`` /
        ``_voice_language`` all see the same object.
        """
        return (
            getattr(self._config, "voice", None) if self._config else None
        )

    def _build_stt_engine(self) -> Any:
        """Build an STTEngine from self._config.voice, or None if
        no voice config is present.

        Returning None is a valid state: the adapter's ``transcribe()``
        returns an empty string in that case, so manual stop + no
        transcription flow still works even on a stock install with
        no STT configured. This keeps the happy path usable for
        recording-only experimentation.
        """
        voice_config = (
            getattr(self._config, "voice", None) if self._config else None
        )
        if voice_config is None:
            return None
        from llm_code.tools.voice import create_stt_engine
        return create_stt_engine(voice_config)

    def _voice_language(self) -> str:
        """Language code passed to the STT engine. Defaults to 'en' when
        no voice config is present or it omits the field."""
        voice_config = (
            getattr(self._config, "voice", None) if self._config else None
        )
        if voice_config is None:
            return "en"
        return getattr(voice_config, "language", "en") or "en"

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
