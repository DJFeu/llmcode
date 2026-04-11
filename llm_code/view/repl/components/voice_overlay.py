"""VoiceOverlay — UI state for active voice recording.

Doesn't have its own Float or Window; drives state that the
StatusLine renders. When active:

- self._active is True
- Status line shows ``🎙 0:02.3 · peak 0.42 · Ctrl+G stop`` in dim red
  (rendered by StatusLine based on voice_active/voice_seconds/voice_peak
  fields in the merged StatusUpdate state)
- Input is visually locked but not blocked at the PT layer — the M9 MVP
  relies on the status-line indicator instead of hard-blocking keys.
  A later milestone can swap in a real ``Buffer.read_only`` filter.

Lifecycle:
    overlay.start()          # Ctrl+G pressed — recorder begins
    overlay.update(s, p)     # each chunk from recorder (background thread
                             # → main loop via call_soon_threadsafe)
    overlay.stop(reason)     # VAD auto-stop, manual Ctrl+G, or error
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from llm_code.view.types import StatusUpdate

if TYPE_CHECKING:
    from llm_code.view.repl.coordinator import ScreenCoordinator


class VoiceOverlay:
    """Small state holder for the voice-active status flip."""

    def __init__(self, coordinator: "ScreenCoordinator") -> None:
        self._coordinator = coordinator
        self._active = False

    @property
    def is_active(self) -> bool:
        return self._active

    def start(self) -> None:
        """Enter voice-active state. Flip the status line into voice mode."""
        if self._active:
            return
        self._active = True
        self._coordinator.update_status(StatusUpdate(
            voice_active=True,
            voice_seconds=0.0,
            voice_peak=0.0,
        ))

    def update(self, seconds: float, peak: float) -> None:
        """Periodic progress update from the recorder.

        Must be called on the main event loop (background-thread
        callers use ``loop.call_soon_threadsafe``).

        ``voice_active=True`` is re-asserted on every call because
        StatusUpdate's default for that field is ``False`` and the
        StatusLine merge treats ``False`` on voice_active as a
        meaningful clear — without re-asserting, each progress tick
        would flip the status line out of voice mode.
        """
        if not self._active:
            return
        self._coordinator.update_status(StatusUpdate(
            voice_active=True,
            voice_seconds=seconds,
            voice_peak=peak,
        ))

    def stop(self, reason: str) -> None:
        """Leave voice-active state. Clear the voice-mode status flip.

        Args:
            reason: free-form tag for callers (``manual_stop``,
                ``vad_auto_stop``, ``no_speech_timeout``, ``error:...``)
                — passed through for telemetry; the overlay itself
                doesn't inspect it.
        """
        if not self._active:
            return
        self._active = False
        self._coordinator.update_status(StatusUpdate(voice_active=False))
