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
