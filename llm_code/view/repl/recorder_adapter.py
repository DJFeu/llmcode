"""Polling-to-callback adapter for ``llm_code.tools.voice.AudioRecorder``.

The real ``AudioRecorder`` exposes a polling API
(``should_auto_stop``, ``_last_peak``, ``elapsed_seconds``). The REPL
backend wants a callback API (``on_chunk_progress`` / ``on_auto_stop``)
so background-thread events can flow through
``asyncio.call_soon_threadsafe`` without the backend having to poll
the recorder itself.

This adapter bridges the two: it wraps a real ``AudioRecorder``,
schedules an asyncio polling task on ``.start()``, and emits callbacks
at a fixed cadence until ``.stop()`` is called or VAD auto-fires. The
adapter also exposes ``.transcribe()`` so the backend's
``_transcribe_and_insert`` coroutine can stay unchanged from M9.

Shipped in M9.5 (2026-04-12). See
``docs/superpowers/plans/2026-04-12-m9.5-tech-debt-cleanup.md``
for the rationale — M9 shipped with a callback-style assumption that
never matched the real polling recorder, so voice didn't actually
work in production v2.0.0 until this adapter landed.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

from llm_code.tools.voice import AudioRecorder

POLL_INTERVAL_SECONDS = 0.2
# 16-bit signed PCM maxes at 32767. ``AudioRecorder._last_peak`` is
# derived from ``abs(int16)`` so it lives in [0, 32767]; dividing by
# this constant normalizes to [0.0, 1.0] for the REPL's status line
# peak meter.
PEAK_NORMALIZE_DIVISOR = 32767.0


class PollingRecorderAdapter:
    """Bridge between ``AudioRecorder``'s polling API and M9 callbacks.

    The backend constructs one of these per voice session. The adapter
    instantiates a real ``AudioRecorder``, starts it, and spins a small
    asyncio task that polls at ~5 Hz, emitting
    ``on_chunk_progress(elapsed, peak_norm)`` each tick and
    ``on_auto_stop(reason)`` exactly once if VAD latches before the
    caller stops manually.

    Transcription is exposed as an async method because the underlying
    STT engines are synchronous HTTP/WebSocket calls — running them on
    ``asyncio.to_thread`` keeps the main loop responsive while Whisper
    is busy.
    """

    def __init__(
        self,
        *,
        on_chunk_progress: Callable[[float, float], None],
        on_auto_stop: Callable[[str], None],
        silence_seconds: float = 2.0,
        stt_engine: Any = None,
        language: str = "en",
        recorder: Optional[AudioRecorder] = None,
    ) -> None:
        self._on_chunk = on_chunk_progress
        self._on_auto = on_auto_stop
        # ``recorder`` injection is for unit tests: production calls do
        # not pass it, and the adapter constructs a real ``AudioRecorder``
        # configured with the requested silence window.
        self._recorder = recorder or AudioRecorder(
            silence_seconds=silence_seconds,
        )
        self._stt = stt_engine
        self._language = language
        self._audio_bytes: bytes = b""
        self._poll_task: Optional[asyncio.Task] = None
        self._started = False
        self._stopped = False

    def start(self) -> None:
        """Begin recording and schedule the background polling task.

        Must be called from a context with a running asyncio event loop
        (the REPL's key-binding dispatcher runs inside prompt_toolkit's
        loop, which satisfies this). Idempotent — subsequent calls are
        no-ops until ``stop()`` is called.
        """
        if self._started:
            return
        self._started = True
        self._recorder.start()
        loop = asyncio.get_running_loop()
        self._poll_task = loop.create_task(self._poll_loop())

    def stop(self) -> None:
        """Manual stop. Cancels the polling task and flushes the recorder.

        Safe to call multiple times; second and later calls are no-ops.
        Also safe to call when ``start()`` has not been invoked.
        """
        if self._stopped:
            return
        self._stopped = True
        if self._poll_task is not None and not self._poll_task.done():
            self._poll_task.cancel()
        if self._started:
            self._audio_bytes = self._recorder.stop()

    async def transcribe(self) -> str:
        """Run STT on the captured audio, returning the transcript.

        Returns the empty string when:
        - No audio was captured (``start()`` never called, or immediate stop).
        - No STT engine was configured.
        - The recorder auto-stopped via the no-speech timeout.
        - The STT engine returns an empty string (normal silence case).

        STT is synchronous — we run it on a thread so the main event
        loop stays responsive during the request.
        """
        if not self._audio_bytes or self._stt is None:
            return ""
        if self._recorder.stopped_no_speech:
            return ""
        return await asyncio.to_thread(
            self._stt.transcribe, self._audio_bytes, self._language,
        )

    @property
    def stopped_no_speech(self) -> bool:
        """Propagate the recorder's no-speech flag so callers can surface
        a targeted "check your mic" hint instead of the generic flow."""
        return self._recorder.stopped_no_speech

    async def _poll_loop(self) -> None:
        """Poll the recorder ~5× per second and emit callbacks.

        Emits ``on_chunk_progress(elapsed, peak_norm)`` each tick with
        the current elapsed time and peak amplitude normalized to
        [0.0, 1.0]. When ``should_auto_stop()`` flips, snapshots the
        audio, sets ``_stopped``, and emits ``on_auto_stop(reason)``
        exactly once before returning.

        The task is cancellable — ``stop()`` cancels it, and the
        ``asyncio.CancelledError`` arm silently exits.
        """
        try:
            while not self._stopped:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                if self._stopped:
                    return
                elapsed = self._recorder.elapsed_seconds()
                raw_peak = self._recorder._last_peak
                peak_norm = min(1.0, raw_peak / PEAK_NORMALIZE_DIVISOR)
                self._on_chunk(elapsed, peak_norm)
                if self._recorder.should_auto_stop():
                    reason = (
                        "no_speech_timeout"
                        if self._recorder.stopped_no_speech
                        else "vad_auto_stop"
                    )
                    self._audio_bytes = self._recorder.stop()
                    self._stopped = True
                    self._on_auto(reason)
                    return
        except asyncio.CancelledError:
            pass


__all__ = ["PollingRecorderAdapter", "POLL_INTERVAL_SECONDS", "PEAK_NORMALIZE_DIVISOR"]
