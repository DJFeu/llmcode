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
    """Silence detection / auto-stop."""

    def _silent_chunk(self, n_samples: int = 1024) -> bytes:
        """Produce a PCM chunk whose samples are all zero (pure silence)."""
        return struct.pack(f"<{n_samples}h", *([0] * n_samples))

    def _loud_chunk(self, n_samples: int = 1024, amp: int = 8000) -> bytes:
        """Produce a PCM chunk alternating +amp / -amp → mean |s| == amp."""
        samples = [amp if i % 2 == 0 else -amp for i in range(n_samples)]
        return struct.pack(f"<{n_samples}h", *samples)

    def test_vad_disabled_never_auto_stops(self):
        rec = AudioRecorder(backend=RecorderBackend.SOUNDDEVICE)
        rec._recording = True
        rec._update_silence_tracker(self._silent_chunk())
        assert rec.should_auto_stop() is False
        assert rec._silence_start is None

    def test_silence_starts_window_when_chunk_is_quiet(self):
        rec = AudioRecorder(
            backend=RecorderBackend.SOUNDDEVICE,
            silence_seconds=0.5,
            silence_threshold=500,
        )
        rec._recording = True
        rec._update_silence_tracker(self._silent_chunk())
        assert rec._silence_start is not None

    def test_loud_chunk_resets_silence_window(self):
        rec = AudioRecorder(
            backend=RecorderBackend.SOUNDDEVICE,
            silence_seconds=0.5,
            silence_threshold=500,
        )
        rec._recording = True
        rec._update_silence_tracker(self._silent_chunk())
        assert rec._silence_start is not None
        rec._update_silence_tracker(self._loud_chunk())
        assert rec._silence_start is None

    def test_should_auto_stop_returns_true_after_window(self, monkeypatch):
        rec = AudioRecorder(
            backend=RecorderBackend.SOUNDDEVICE,
            silence_seconds=0.1,
            silence_threshold=500,
        )
        rec._recording = True

        fake_now = [100.0]
        monkeypatch.setattr(
            "llm_code.tools.voice.time.monotonic",
            lambda: fake_now[0],
        )

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
        rec._update_silence_tracker(b"\x00\x00\x00\x00\x00")
        assert rec._silence_start is not None

    def test_update_silence_tracker_noop_on_empty_chunk(self):
        rec = AudioRecorder(
            backend=RecorderBackend.SOUNDDEVICE,
            silence_seconds=0.5,
        )
        rec._recording = True
        rec._update_silence_tracker(b"")
        assert rec._silence_start is None
