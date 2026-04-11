"""Unit tests for ``PollingRecorderAdapter``.

Exercises the polling→callback bridge in isolation using a
``FakePollingAudioRecorder`` that mirrors the real
``AudioRecorder``'s polling surface. The adapter's real collaborator
(``llm_code.tools.voice.AudioRecorder``) is never constructed here —
we pass the fake in via the ``recorder=`` kwarg so the tests can
deterministically drive elapsed time, peak amplitude, and auto-stop
latching without touching real audio hardware.

These tests complement the M9 e2e coverage in
``tests/test_e2e_repl/test_voice_flow.py``, which exercises the
adapter's seams via the full REPL backend.
"""
from __future__ import annotations

import asyncio
from typing import List, Tuple

import pytest

from llm_code.view.repl.recorder_adapter import (
    POLL_INTERVAL_SECONDS,
    PollingRecorderAdapter,
)


class FakePollingAudioRecorder:
    """Test double mirroring the real ``AudioRecorder`` polling surface.

    Exposes the same attributes/methods the adapter reads from, but
    with deterministic backing state so tests can set a fake
    ``_last_peak`` / ``elapsed_seconds`` / ``should_auto_stop`` and
    observe how the adapter reacts.
    """

    def __init__(self, **kwargs) -> None:
        self.silence_seconds: float = kwargs.get("silence_seconds", 0.0)
        self._recording = False
        self._started = False
        self._stopped = False
        self._last_peak: int = 0
        self._elapsed: float = 0.0
        self._should_stop = False
        self.stopped_no_speech = False
        self.auto_stopped = False

    def start(self) -> None:
        self._recording = True
        self._started = True

    def stop(self) -> bytes:
        self._recording = False
        self._stopped = True
        # Return a deterministic non-empty blob so
        # ``adapter.transcribe()`` sees something to feed to the STT.
        return b"PCM" * 100

    def elapsed_seconds(self) -> float:
        return self._elapsed

    def should_auto_stop(self) -> bool:
        return self._should_stop


class FakeSTT:
    """Synchronous transcribe stub used by the STT path tests."""

    def __init__(self, text: str = "hello world") -> None:
        self.text = text
        self.calls: List[Tuple[bytes, str]] = []

    def transcribe(self, audio_bytes: bytes, language: str) -> str:
        self.calls.append((audio_bytes, language))
        return self.text


# === Construction ===


def test_adapter_constructs_with_injected_recorder() -> None:
    """Injecting a recorder must not trigger real AudioRecorder init."""
    rec = FakePollingAudioRecorder()
    adapter = PollingRecorderAdapter(
        on_chunk_progress=lambda s, p: None,
        on_auto_stop=lambda r: None,
        recorder=rec,
    )
    assert adapter._recorder is rec
    assert adapter._started is False
    assert adapter._stopped is False


# === Start / stop lifecycle ===


@pytest.mark.asyncio
async def test_start_is_idempotent() -> None:
    rec = FakePollingAudioRecorder()
    adapter = PollingRecorderAdapter(
        on_chunk_progress=lambda s, p: None,
        on_auto_stop=lambda r: None,
        recorder=rec,
    )
    adapter.start()
    adapter.start()  # second call is a no-op
    assert rec._started is True
    # Only one poll task should have been scheduled.
    assert adapter._poll_task is not None
    adapter.stop()
    await asyncio.sleep(0)  # let cancellation propagate


@pytest.mark.asyncio
async def test_stop_before_start_is_safe() -> None:
    rec = FakePollingAudioRecorder()
    adapter = PollingRecorderAdapter(
        on_chunk_progress=lambda s, p: None,
        on_auto_stop=lambda r: None,
        recorder=rec,
    )
    adapter.stop()  # never started — should not crash
    assert adapter._stopped is True
    assert rec._stopped is False


@pytest.mark.asyncio
async def test_manual_stop_cancels_poll_task_and_flushes() -> None:
    rec = FakePollingAudioRecorder()
    adapter = PollingRecorderAdapter(
        on_chunk_progress=lambda s, p: None,
        on_auto_stop=lambda r: None,
        recorder=rec,
    )
    adapter.start()
    adapter.stop()
    await asyncio.sleep(0)  # let cancellation propagate
    assert adapter._stopped is True
    assert rec._stopped is True
    # The adapter should have captured the fake recorder's bytes.
    assert adapter._audio_bytes == b"PCM" * 100


# === Poll loop emits chunks ===


@pytest.mark.asyncio
async def test_poll_loop_emits_chunk_progress_with_normalized_peak() -> None:
    """After one poll tick, the on_chunk_progress callback fires once
    with the current elapsed time and peak normalized to [0, 1]."""
    rec = FakePollingAudioRecorder()
    rec._elapsed = 1.5
    rec._last_peak = 16383  # ~half of 32767
    events: List[Tuple[float, float]] = []

    adapter = PollingRecorderAdapter(
        on_chunk_progress=lambda s, p: events.append((s, p)),
        on_auto_stop=lambda r: None,
        recorder=rec,
    )
    adapter.start()
    # Wait long enough for one poll tick.
    await asyncio.sleep(POLL_INTERVAL_SECONDS * 1.5)
    adapter.stop()
    await asyncio.sleep(0)

    assert len(events) >= 1
    seconds, peak_norm = events[0]
    assert seconds == pytest.approx(1.5)
    assert peak_norm == pytest.approx(16383 / 32767.0, rel=1e-3)
    assert 0.0 <= peak_norm <= 1.0


@pytest.mark.asyncio
async def test_peak_normalization_caps_at_one() -> None:
    """Defensive: even an out-of-range peak stays ≤ 1.0."""
    rec = FakePollingAudioRecorder()
    rec._last_peak = 999999  # absurd value
    events: List[Tuple[float, float]] = []

    adapter = PollingRecorderAdapter(
        on_chunk_progress=lambda s, p: events.append((s, p)),
        on_auto_stop=lambda r: None,
        recorder=rec,
    )
    adapter.start()
    await asyncio.sleep(POLL_INTERVAL_SECONDS * 1.5)
    adapter.stop()
    await asyncio.sleep(0)

    assert events, "expected at least one chunk event"
    _, peak_norm = events[0]
    assert peak_norm == 1.0


# === Auto-stop path ===


@pytest.mark.asyncio
async def test_poll_loop_emits_auto_stop_with_vad_reason() -> None:
    rec = FakePollingAudioRecorder()
    rec._should_stop = True
    reasons: List[str] = []
    events: List[Tuple[float, float]] = []

    adapter = PollingRecorderAdapter(
        on_chunk_progress=lambda s, p: events.append((s, p)),
        on_auto_stop=lambda r: reasons.append(r),
        recorder=rec,
    )
    adapter.start()
    await asyncio.sleep(POLL_INTERVAL_SECONDS * 1.5)
    # Poll loop should have latched auto-stop and exited on its own.
    assert reasons == ["vad_auto_stop"]
    assert adapter._stopped is True
    # Subsequent manual stop is a no-op (idempotent)
    adapter.stop()


@pytest.mark.asyncio
async def test_poll_loop_reports_no_speech_reason() -> None:
    rec = FakePollingAudioRecorder()
    rec._should_stop = True
    rec.stopped_no_speech = True
    reasons: List[str] = []

    adapter = PollingRecorderAdapter(
        on_chunk_progress=lambda s, p: None,
        on_auto_stop=lambda r: reasons.append(r),
        recorder=rec,
    )
    adapter.start()
    await asyncio.sleep(POLL_INTERVAL_SECONDS * 1.5)
    assert reasons == ["no_speech_timeout"]


@pytest.mark.asyncio
async def test_auto_stop_fires_exactly_once() -> None:
    """Even if we wait well past the first latch, on_auto_stop runs once."""
    rec = FakePollingAudioRecorder()
    rec._should_stop = True
    reasons: List[str] = []

    adapter = PollingRecorderAdapter(
        on_chunk_progress=lambda s, p: None,
        on_auto_stop=lambda r: reasons.append(r),
        recorder=rec,
    )
    adapter.start()
    await asyncio.sleep(POLL_INTERVAL_SECONDS * 3)
    assert reasons == ["vad_auto_stop"]


# === transcribe() ===


@pytest.mark.asyncio
async def test_transcribe_returns_empty_when_no_stt_configured() -> None:
    rec = FakePollingAudioRecorder()
    adapter = PollingRecorderAdapter(
        on_chunk_progress=lambda s, p: None,
        on_auto_stop=lambda r: None,
        recorder=rec,
        stt_engine=None,
    )
    adapter.start()
    adapter.stop()
    await asyncio.sleep(0)
    assert await adapter.transcribe() == ""


@pytest.mark.asyncio
async def test_transcribe_calls_stt_with_captured_audio_and_language() -> None:
    rec = FakePollingAudioRecorder()
    stt = FakeSTT(text="hi there")
    adapter = PollingRecorderAdapter(
        on_chunk_progress=lambda s, p: None,
        on_auto_stop=lambda r: None,
        recorder=rec,
        stt_engine=stt,
        language="zh",
    )
    adapter.start()
    adapter.stop()
    await asyncio.sleep(0)

    text = await adapter.transcribe()
    assert text == "hi there"
    assert stt.calls == [(b"PCM" * 100, "zh")]


@pytest.mark.asyncio
async def test_transcribe_returns_empty_when_stopped_no_speech() -> None:
    rec = FakePollingAudioRecorder()
    rec.stopped_no_speech = True
    stt = FakeSTT(text="should not be returned")
    adapter = PollingRecorderAdapter(
        on_chunk_progress=lambda s, p: None,
        on_auto_stop=lambda r: None,
        recorder=rec,
        stt_engine=stt,
    )
    adapter.start()
    adapter.stop()
    await asyncio.sleep(0)

    assert await adapter.transcribe() == ""
    assert stt.calls == []


# === stopped_no_speech passthrough ===


def test_stopped_no_speech_property_passes_through() -> None:
    rec = FakePollingAudioRecorder()
    rec.stopped_no_speech = True
    adapter = PollingRecorderAdapter(
        on_chunk_progress=lambda s, p: None,
        on_auto_stop=lambda r: None,
        recorder=rec,
    )
    assert adapter.stopped_no_speech is True
