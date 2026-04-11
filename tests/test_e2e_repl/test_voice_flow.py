"""End-to-end voice flow tests — exercise the Ctrl+G toggle path.

The real ``llm_code.tools.voice.AudioRecorder`` uses a polling API
(``should_auto_stop``, ``_last_peak``) rather than callbacks. M9.5
introduced :class:`PollingRecorderAdapter` to bridge that polling
surface to the callback shape the REPL backend expects. These tests
monkeypatch the adapter at the ``recorder_adapter`` module level with
a callback-compatible ``FakeRecorder`` so the backend glue exercises
its full code path without touching real audio hardware.

Patching the adapter directly (rather than the underlying
``AudioRecorder``) keeps the fake simple: the M9 callback contract
is unchanged, so we don't have to re-implement polling semantics in
the fake.
"""
from __future__ import annotations

import asyncio
import io
from typing import Any, Callable, Optional

import pytest
import pytest_asyncio
from rich.console import Console

from llm_code.view.repl.backend import REPLBackend


class FakeRecorder:
    """Callback-compatible recorder mock that stands in for
    :class:`PollingRecorderAdapter`.

    Accepts ``on_chunk_progress`` / ``on_auto_stop`` kwargs the same
    way the real adapter does, and silently ignores the other adapter
    kwargs (``silence_seconds``, ``stt_engine``, ``language``,
    ``recorder``) via ``**kwargs``. Tests use ``emit_chunk`` /
    ``emit_auto_stop`` to simulate the adapter's poll loop without
    running a real asyncio task.
    """

    def __init__(self, **kwargs: Any) -> None:
        self.started = False
        self.stopped = False
        self._on_chunk: Optional[Callable[[float, float], None]] = kwargs.get(
            "on_chunk_progress"
        )
        self._on_auto: Optional[Callable[[str], None]] = kwargs.get(
            "on_auto_stop"
        )
        self.transcription: str = "hello from fake"

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    async def transcribe(self) -> str:
        return self.transcription

    # Helpers the tests use to simulate background-thread events.
    def emit_chunk(self, seconds: float, peak: float) -> None:
        if self._on_chunk is not None:
            self._on_chunk(seconds, peak)

    def emit_auto_stop(self, reason: str) -> None:
        if self._on_auto is not None:
            self._on_auto(reason)


@pytest_asyncio.fixture
async def voice_pilot():
    """REPLBackend with a captured StringIO console, started cleanly."""
    capture = io.StringIO()
    console = Console(
        file=capture,
        force_terminal=True,
        color_system="truecolor",
        width=80,
    )
    backend = REPLBackend(console=console)
    await backend.start()
    try:
        yield backend, capture
    finally:
        await backend.stop()


def _patch_recorder(monkeypatch, cls=FakeRecorder) -> None:
    """Replace ``PollingRecorderAdapter`` with a callback-style fake.

    Must patch the adapter at its defining module so that backend's
    lazy ``from llm_code.view.repl.recorder_adapter import
    PollingRecorderAdapter`` picks up the fake.
    """
    from llm_code.view.repl import recorder_adapter

    monkeypatch.setattr(recorder_adapter, "PollingRecorderAdapter", cls)


# === Toggle lifecycle ===


@pytest.mark.asyncio
async def test_ctrl_g_starts_recording(voice_pilot, monkeypatch):
    _patch_recorder(monkeypatch)
    backend, _ = voice_pilot

    backend._toggle_voice()

    assert backend._voice_active is True
    assert backend._recorder is not None
    assert backend._recorder.started is True
    assert backend._coordinator.current_status.voice_active is True


@pytest.mark.asyncio
async def test_second_ctrl_g_stops_recording(voice_pilot, monkeypatch):
    _patch_recorder(monkeypatch)
    backend, _ = voice_pilot

    backend._toggle_voice()  # start
    backend._toggle_voice()  # stop
    await asyncio.sleep(0.02)

    assert backend._voice_active is False
    assert backend._recorder.stopped is True
    assert backend._coordinator.current_status.voice_active is False


@pytest.mark.asyncio
async def test_manual_stop_transcribes_and_inserts(voice_pilot, monkeypatch):
    _patch_recorder(monkeypatch)
    backend, _ = voice_pilot

    backend._toggle_voice()
    backend._toggle_voice()
    await asyncio.sleep(0.02)  # let the scheduled transcribe task run

    buffer_text = backend._coordinator._input_area.buffer.text
    assert "hello from fake" in buffer_text


# === VAD auto-stop path ===


@pytest.mark.asyncio
async def test_vad_auto_stop_transcribes_and_inserts(
    voice_pilot, monkeypatch,
):
    _patch_recorder(monkeypatch)
    backend, _ = voice_pilot

    backend._toggle_voice()
    # Simulate VAD auto-stop from the recorder's background thread.
    backend._on_recorder_auto_stop(reason="vad_auto_stop")
    # Two scheduled tasks: voice_stopped + transcribe — yield enough.
    await asyncio.sleep(0.05)

    assert backend._voice_active is False
    assert backend._coordinator.current_status.voice_active is False
    buffer_text = backend._coordinator._input_area.buffer.text
    assert "hello from fake" in buffer_text


@pytest.mark.asyncio
async def test_vad_auto_stop_no_speech_reason(voice_pilot, monkeypatch):
    """The 'no_speech_timeout' reason also routes through the same path."""
    _patch_recorder(monkeypatch)
    backend, _ = voice_pilot

    backend._toggle_voice()
    backend._on_recorder_auto_stop(reason="no_speech_timeout")
    await asyncio.sleep(0.05)

    assert backend._coordinator.current_status.voice_active is False


# === Background-thread progress updates ===


@pytest.mark.asyncio
async def test_chunk_progress_updates_status(voice_pilot, monkeypatch):
    """Background-thread chunks route via call_soon_threadsafe and update status."""
    _patch_recorder(monkeypatch)
    backend, _ = voice_pilot

    backend._toggle_voice()
    # Emit a chunk as if from the background audio callback.
    backend._on_recorder_chunk(seconds=1.5, peak=0.42)
    # call_soon_threadsafe schedules on the main loop — yield to let it run.
    await asyncio.sleep(0.02)

    s = backend._coordinator.current_status
    assert s.voice_seconds == 1.5
    assert s.voice_peak == 0.42
    assert s.voice_active is True

    backend._toggle_voice()  # cleanup
    await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_progress_while_inactive_ignored(voice_pilot, monkeypatch):
    """Chunk callbacks after stop() must not re-activate the voice UI."""
    _patch_recorder(monkeypatch)
    backend, _ = voice_pilot

    # Start and stop; now inactive.
    backend._toggle_voice()
    backend._toggle_voice()
    await asyncio.sleep(0.02)
    # Late callback from a still-draining background thread.
    backend._on_recorder_chunk(seconds=9.9, peak=0.99)
    await asyncio.sleep(0.02)

    assert backend._coordinator.current_status.voice_active is False


# === Transcription error path ===


@pytest.mark.asyncio
async def test_transcription_error_shows_error(voice_pilot, monkeypatch):
    class FailingRecorder(FakeRecorder):
        async def transcribe(self) -> str:
            raise RuntimeError("stt api down")

    _patch_recorder(monkeypatch, cls=FailingRecorder)
    backend, capture = voice_pilot

    backend._toggle_voice()
    backend._toggle_voice()
    await asyncio.sleep(0.05)

    assert "transcription failed" in capture.getvalue().lower()
    # Buffer remains empty because transcription never produced text.
    assert backend._coordinator._input_area.buffer.text == ""


@pytest.mark.asyncio
async def test_silent_recording_inserts_nothing(voice_pilot, monkeypatch):
    class SilentRecorder(FakeRecorder):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.transcription = ""  # override parent's default

    _patch_recorder(monkeypatch, cls=SilentRecorder)
    backend, _ = voice_pilot

    backend._toggle_voice()
    backend._toggle_voice()
    await asyncio.sleep(0.05)

    assert backend._coordinator._input_area.buffer.text == ""


# === Recorder init failure ===


@pytest.mark.asyncio
async def test_recorder_init_failure_prints_error(voice_pilot, monkeypatch):
    class BrokenRecorder:
        def __init__(self, **kwargs):
            raise OSError("permission denied")

    _patch_recorder(monkeypatch, cls=BrokenRecorder)
    backend, capture = voice_pilot

    backend._toggle_voice()

    assert "voice unavailable" in capture.getvalue().lower()
    assert backend._voice_active is False
    assert backend._recorder is None


# === Idempotent stop when inactive ===


@pytest.mark.asyncio
async def test_stop_voice_when_inactive_is_noop(voice_pilot, monkeypatch):
    _patch_recorder(monkeypatch)
    backend, _ = voice_pilot
    # No start — call stop directly.
    backend._stop_voice()
    assert backend._voice_active is False


# === Toggle 10× stress (R3 risk smoke test) ===


@pytest.mark.asyncio
async def test_toggle_stress_no_deadlock(voice_pilot, monkeypatch):
    """Spec section 10.1 R3: voice + asyncio.Lock deadlock risk.

    Plan suggests 100× stress; 10× is enough for CI speed while still
    exercising the start/stop cycle and the background-thread forwarding.
    """
    _patch_recorder(monkeypatch)
    backend, _ = voice_pilot

    for _ in range(10):
        backend._toggle_voice()  # start
        backend._on_recorder_chunk(seconds=0.1, peak=0.05)
        backend._toggle_voice()  # stop
        await asyncio.sleep(0.01)

    assert backend._voice_active is False


# === voice_progress with zero values ===


@pytest.mark.asyncio
async def test_progress_with_zero_values_still_updates(voice_pilot, monkeypatch):
    _patch_recorder(monkeypatch)
    backend, _ = voice_pilot

    backend._toggle_voice()
    backend._on_recorder_chunk(seconds=0.0, peak=0.0)
    await asyncio.sleep(0.02)

    s = backend._coordinator.current_status
    assert s.voice_active is True
    assert s.voice_seconds == 0.0
    assert s.voice_peak == 0.0

    backend._toggle_voice()
    await asyncio.sleep(0.02)
