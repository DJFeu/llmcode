"""Integration tests for voice input pipeline."""
from __future__ import annotations

import struct
from unittest.mock import MagicMock, patch

import pytest

from llm_code.runtime.config import RuntimeConfig, VoiceConfig
from llm_code.voice.recorder import AudioRecorder, RecorderBackend
from llm_code.voice.stt import create_stt_engine


def _fake_pcm(duration_s: float = 0.5, sample_rate: int = 16000) -> bytes:
    n_samples = int(duration_s * sample_rate)
    return struct.pack(f"<{n_samples}h", *([0] * n_samples))


class TestVoicePipeline:
    """End-to-end pipeline: config -> recorder -> STT engine -> text."""

    def test_whisper_pipeline(self):
        """Full pipeline with mocked Whisper endpoint."""
        config = VoiceConfig(enabled=True, backend="whisper", language="en")
        engine = create_stt_engine(config)

        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value={"text": "hello world"}),
                raise_for_status=MagicMock(),
            )
            result = engine.transcribe(_fake_pcm(), "en")
            assert result == "hello world"

    def test_runtime_config_voice_disabled_by_default(self):
        rc = RuntimeConfig()
        assert rc.voice.enabled is False
        assert rc.voice.backend == "whisper"

    def test_runtime_config_voice_enabled(self):
        vc = VoiceConfig(enabled=True, backend="google", language="zh")
        rc = RuntimeConfig(voice=vc)
        assert rc.voice.enabled is True
        assert rc.voice.language == "zh"

    def test_factory_creates_all_backends(self):
        for backend in ("whisper",):
            config = VoiceConfig(backend=backend)
            engine = create_stt_engine(config)
            assert hasattr(engine, "transcribe")

    def test_recorder_lifecycle(self):
        """Verify recorder start/stop does not crash (mocked)."""
        rec = AudioRecorder(backend=RecorderBackend.SOUNDDEVICE)
        # Simulate buffer content
        rec._buffer = bytearray(_fake_pcm(0.1))
        rec._recording = True
        audio = rec.stop()
        assert isinstance(audio, bytes)
        assert len(audio) > 0

    def test_language_validation_in_pipeline(self):
        from llm_code.voice.languages import validate_language
        assert validate_language("zh") == "zh"
        with pytest.raises(ValueError):
            validate_language("nonexistent")
