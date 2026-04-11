"""Tests for AudioRecorder."""
from __future__ import annotations

import struct
from unittest.mock import MagicMock, patch

import pytest

from llm_code.voice.recorder import AudioRecorder, RecorderBackend, detect_backend


class TestDetectBackend:
    @patch("shutil.which", return_value=None)
    def test_sounddevice_preferred(self, _mock_which):
        with patch.dict("sys.modules", {"sounddevice": MagicMock()}):
            assert detect_backend() == RecorderBackend.SOUNDDEVICE

    @patch("shutil.which", side_effect=lambda cmd: "/usr/bin/sox" if cmd == "sox" else None)
    def test_sox_fallback(self, _mock_which):
        with patch.dict("sys.modules", {"sounddevice": None}):
            # Force ImportError on sounddevice
            import sys
            sys.modules["sounddevice"] = None  # type: ignore[assignment]
            try:
                assert detect_backend() == RecorderBackend.SOX
            finally:
                sys.modules.pop("sounddevice", None)

    @patch("shutil.which", return_value=None)
    def test_no_backend_raises(self, _mock_which):
        with patch.dict("sys.modules", {"sounddevice": None}):
            import sys
            sys.modules["sounddevice"] = None  # type: ignore[assignment]
            try:
                with pytest.raises(RuntimeError, match="No audio recording backend"):
                    detect_backend()
            finally:
                sys.modules.pop("sounddevice", None)


class TestAudioRecorder:
    def test_init(self):
        rec = AudioRecorder(backend=RecorderBackend.SOUNDDEVICE)
        assert rec.sample_rate == 16000
        assert rec.channels == 1

    def test_start_stop_returns_bytes(self):
        """Mock sounddevice to simulate recording."""
        mock_sd = MagicMock()
        # Simulate 0.1s of silence (1600 samples at 16kHz)
        samples = [0.0] * 1600
        raw = struct.pack(f"<{len(samples)}h", *[int(s * 32767) for s in samples])

        rec = AudioRecorder(backend=RecorderBackend.SOUNDDEVICE)
        with patch.dict("sys.modules", {"sounddevice": mock_sd}):
            rec._buffer = bytearray(raw)
            rec._recording = True
            result = rec.stop()
            assert isinstance(result, bytes)
            assert len(result) > 0

    def test_stop_without_start_returns_empty(self):
        rec = AudioRecorder(backend=RecorderBackend.SOUNDDEVICE)
        result = rec.stop()
        assert result == b""

    def test_elapsed_time(self):
        rec = AudioRecorder(backend=RecorderBackend.SOUNDDEVICE)
        # Not recording
        assert rec.elapsed_seconds() == 0.0


class TestAudioRecorderVAD:
    """Peak-based silence detection / auto-stop."""

    def _silent_chunk(self, n_samples: int = 1024, noise_floor: int = 0) -> bytes:
        """Produce a quiet PCM chunk — all samples at ±noise_floor."""
        samples = [
            noise_floor if i % 2 == 0 else -noise_floor for i in range(n_samples)
        ]
        return struct.pack(f"<{n_samples}h", *samples)

    def _loud_chunk(self, n_samples: int = 1024, peak: int = 10000) -> bytes:
        """Produce a chunk with a speech-like peak amplitude."""
        samples = [peak if i % 2 == 0 else -peak for i in range(n_samples)]
        return struct.pack(f"<{n_samples}h", *samples)

    def test_vad_disabled_never_auto_stops(self):
        rec = AudioRecorder(backend=RecorderBackend.SOUNDDEVICE)
        rec._recording = True
        rec._update_silence_tracker(self._silent_chunk())
        assert rec.should_auto_stop() is False
        assert rec._silence_start is None

    def test_silence_starts_window_when_chunk_is_quiet_after_speech(self):
        """After the speech gate has flipped, a silent chunk starts
        the silence window. Without prior speech the gate blocks
        window start — see TestAudioRecorderSpeechGate."""
        rec = AudioRecorder(
            backend=RecorderBackend.SOUNDDEVICE,
            silence_seconds=0.5,
            silence_threshold=3000,
        )
        rec._recording = True
        # Flip the gate with a speech-level peak first.
        rec._update_silence_tracker(self._loud_chunk(peak=8000))
        # Then go silent — window starts.
        rec._update_silence_tracker(self._silent_chunk(noise_floor=100))
        assert rec._silence_start is not None

    def test_noisy_room_below_peak_threshold_still_silent(self):
        """After speech has been heard, a noisy-but-sub-threshold
        chunk (peak ~1500 on a MacBook with fan hum) still counts
        as silence and advances the silence window."""
        rec = AudioRecorder(
            backend=RecorderBackend.SOUNDDEVICE,
            silence_seconds=0.5,
            silence_threshold=3000,
        )
        rec._recording = True
        # Speech first, then the noisy-room chunk.
        rec._update_silence_tracker(self._loud_chunk(peak=8000))
        rec._update_silence_tracker(self._loud_chunk(peak=1500))
        assert rec._silence_start is not None
        assert rec._last_peak == 1500

    def test_loud_chunk_resets_silence_window(self):
        """A speech-like peak above the threshold clears the window."""
        rec = AudioRecorder(
            backend=RecorderBackend.SOUNDDEVICE,
            silence_seconds=0.5,
            silence_threshold=3000,
        )
        rec._recording = True
        # Speech first, then silence → window starts.
        rec._update_silence_tracker(self._loud_chunk(peak=8000))
        rec._update_silence_tracker(self._silent_chunk(noise_floor=200))
        assert rec._silence_start is not None
        # Louder speech again — window cleared.
        rec._update_silence_tracker(self._loud_chunk(peak=10000))
        assert rec._silence_start is None
        assert rec._last_peak == 10000

    def test_should_auto_stop_returns_true_after_window(self, monkeypatch):
        rec = AudioRecorder(
            backend=RecorderBackend.SOUNDDEVICE,
            silence_seconds=0.1,
            silence_threshold=3000,
        )
        rec._recording = True
        rec._start_time = 100.0

        fake_now = [100.0]
        monkeypatch.setattr(
            "llm_code.tools.voice.time.monotonic",
            lambda: fake_now[0],
        )

        # Flip the gate first — without this the speech-gate blocks
        # the silence window from starting at all.
        rec._update_silence_tracker(self._loud_chunk(peak=8000))
        rec._update_silence_tracker(self._silent_chunk())
        assert rec.should_auto_stop() is False

        fake_now[0] += 0.2
        assert rec.should_auto_stop() is True
        assert rec.auto_stopped is True

    def test_should_auto_stop_false_when_not_recording(self):
        rec = AudioRecorder(
            backend=RecorderBackend.SOUNDDEVICE,
            silence_seconds=0.1,
        )
        assert rec.should_auto_stop() is False

    def test_update_silence_tracker_ignores_odd_byte_count(self):
        """Defensive: an odd number of bytes can't be unpacked as int16
        cleanly; tracker must discard the trailing half-sample instead
        of raising."""
        rec = AudioRecorder(
            backend=RecorderBackend.SOUNDDEVICE,
            silence_seconds=0.5,
        )
        rec._recording = True
        # Must not raise even with an odd-length chunk.
        rec._update_silence_tracker(b"\x00\x00\x00\x00\x00")
        # Window is NOT started because the speech gate is still
        # closed (these 5 bytes are pure silence and no speech has
        # been heard yet).
        assert rec._silence_start is None

    def test_update_silence_tracker_noop_on_empty_chunk(self):
        rec = AudioRecorder(
            backend=RecorderBackend.SOUNDDEVICE,
            silence_seconds=0.5,
        )
        rec._recording = True
        rec._update_silence_tracker(b"")
        assert rec._silence_start is None
        assert rec._last_peak == 0

    def test_last_peak_and_mean_updated_for_instrumentation(self):
        """The `/voice` status command shows these values so users
        can tune silence_threshold — they must reflect the most recent
        chunk, not be stuck at zero."""
        rec = AudioRecorder(
            backend=RecorderBackend.SOUNDDEVICE,
            silence_seconds=0.5,
            silence_threshold=3000,
        )
        rec._recording = True
        rec._update_silence_tracker(self._loud_chunk(peak=7500))
        assert rec._last_peak == 7500
        assert rec._last_mean == 7500


class TestAudioRecorderSpeechGate:
    """The VAD window should only start counting silence *after* the
    first real speech chunk is observed. Without this gate, pressing
    the hotkey in a silent room (or with microphone access denied)
    fires auto-stop immediately because every chunk reads as silence
    from byte zero."""

    def _silent_chunk(self, n_samples: int = 1024) -> bytes:
        return struct.pack(f"<{n_samples}h", *([0] * n_samples))

    def _loud_chunk(self, n_samples: int = 1024, peak: int = 10000) -> bytes:
        samples = [peak if i % 2 == 0 else -peak for i in range(n_samples)]
        return struct.pack(f"<{n_samples}h", *samples)

    def test_silence_before_speech_does_not_start_window(self, monkeypatch):
        """10 silent chunks in a row must NOT start a silence window
        because the user hasn't said anything yet."""
        rec = AudioRecorder(
            backend=RecorderBackend.SOUNDDEVICE,
            silence_seconds=0.1,
            silence_threshold=3000,
        )
        rec._recording = True
        rec._start_time = 100.0
        monkeypatch.setattr(
            "llm_code.tools.voice.time.monotonic", lambda: 100.5
        )

        for _ in range(10):
            rec._update_silence_tracker(self._silent_chunk())
        assert rec._has_heard_speech is False
        assert rec._silence_start is None
        # Even though silence_seconds=0.1 has elapsed, auto-stop
        # must NOT fire because we never heard anything.
        assert rec.should_auto_stop() is False

    def test_speech_then_silence_starts_window_normally(self, monkeypatch):
        """Once we've heard at least one loud chunk, subsequent
        silence should start the window as expected."""
        rec = AudioRecorder(
            backend=RecorderBackend.SOUNDDEVICE,
            silence_seconds=0.1,
            silence_threshold=3000,
        )
        rec._recording = True
        rec._start_time = 100.0
        fake_now = [100.0]
        monkeypatch.setattr(
            "llm_code.tools.voice.time.monotonic", lambda: fake_now[0]
        )

        # Hear speech first — flips the gate.
        rec._update_silence_tracker(self._loud_chunk(peak=8000))
        assert rec._has_heard_speech is True

        # Then go silent — window starts.
        fake_now[0] = 100.1
        rec._update_silence_tracker(self._silent_chunk())
        assert rec._silence_start is not None

        # Advance past the window — auto-stop.
        fake_now[0] = 100.3
        assert rec.should_auto_stop() is True
        assert rec._stopped_no_speech is False  # normal stop, not timeout

    def test_no_speech_hard_timeout(self, monkeypatch):
        """If the recorder never hears anything at all within
        _NO_SPEECH_TIMEOUT_SECONDS, it should force-stop and flag
        ``stopped_no_speech=True`` so the caller can tell the user
        to check microphone permission."""
        rec = AudioRecorder(
            backend=RecorderBackend.SOUNDDEVICE,
            silence_seconds=0.1,
            silence_threshold=3000,
        )
        # Shorten the timeout so the test doesn't take 30s.
        rec._NO_SPEECH_TIMEOUT_SECONDS = 1.0
        rec._recording = True
        rec._start_time = 100.0

        fake_now = [100.0]
        monkeypatch.setattr(
            "llm_code.tools.voice.time.monotonic", lambda: fake_now[0]
        )

        # Feed silence, stay under the timeout — no stop.
        rec._update_silence_tracker(self._silent_chunk())
        assert rec.should_auto_stop() is False
        assert rec.stopped_no_speech is False

        # Advance past the timeout.
        fake_now[0] = 101.5
        assert rec.should_auto_stop() is True
        assert rec.stopped_no_speech is True
        assert rec.auto_stopped is True

    def test_speech_clears_silence_window_and_unlatches_restart(self, monkeypatch):
        """Typical "speak, pause briefly, speak again" pattern: the
        pause shouldn't latch silence if the user resumes speaking
        before the window elapses."""
        rec = AudioRecorder(
            backend=RecorderBackend.SOUNDDEVICE,
            silence_seconds=1.0,
            silence_threshold=3000,
        )
        rec._recording = True
        rec._start_time = 100.0
        fake_now = [100.0]
        monkeypatch.setattr(
            "llm_code.tools.voice.time.monotonic", lambda: fake_now[0]
        )

        rec._update_silence_tracker(self._loud_chunk(peak=8000))
        fake_now[0] = 100.5
        rec._update_silence_tracker(self._silent_chunk())
        assert rec._silence_start is not None  # pause started

        # Resume speaking within the window.
        fake_now[0] = 100.9
        rec._update_silence_tracker(self._loud_chunk(peak=9000))
        # Window reset.
        assert rec._silence_start is None
        assert rec.should_auto_stop() is False

    def test_start_resets_speech_gate(self):
        """Starting a fresh recording must clear has_heard_speech /
        stopped_no_speech / silence_start from a previous session."""
        rec = AudioRecorder(
            backend=RecorderBackend.SOUNDDEVICE,
            silence_seconds=0.5,
            silence_threshold=3000,
        )
        # Dirty state from a hypothetical previous recording.
        rec._has_heard_speech = True
        rec._stopped_no_speech = True
        rec._silence_start = 99.0
        rec._last_peak = 8000

        # Patch the external startup branches so start() is callable
        # without actually opening a sounddevice stream.
        rec._backend = RecorderBackend.SOX
        rec._start_external = lambda cmd: None  # type: ignore[assignment]
        rec.start()

        assert rec._has_heard_speech is False
        assert rec._stopped_no_speech is False
        assert rec._silence_start is None
        assert rec._last_peak == 0
