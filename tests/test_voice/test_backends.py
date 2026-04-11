"""Tests for STT backend implementations."""
from __future__ import annotations

import struct
from unittest.mock import MagicMock, patch

import pytest

from llm_code.voice.stt_whisper import WhisperSTT
from llm_code.voice.stt_google import GoogleSTT
from llm_code.voice.stt_anthropic import AnthropicSTT


def _fake_pcm(duration_s: float = 0.1, sample_rate: int = 16000) -> bytes:
    """Generate silent PCM bytes for testing."""
    n_samples = int(duration_s * sample_rate)
    return struct.pack(f"<{n_samples}h", *([0] * n_samples))


class TestWhisperSTT:
    def test_implements_protocol(self):
        from llm_code.voice.stt import STTEngine
        assert isinstance(WhisperSTT(url="http://localhost:8000/v1/audio/transcriptions"), STTEngine)

    @patch("httpx.post")
    def test_transcribe_success(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"text": "hello world"}),
            raise_for_status=MagicMock(),
        )
        engine = WhisperSTT(url="http://localhost:8000/v1/audio/transcriptions")
        result = engine.transcribe(_fake_pcm(), "en")
        assert result == "hello world"
        mock_post.assert_called_once()

    @patch("httpx.post")
    def test_transcribe_error_raises(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=500,
            raise_for_status=MagicMock(side_effect=Exception("Server error")),
        )
        engine = WhisperSTT(url="http://localhost:8000/v1/audio/transcriptions")
        with pytest.raises(Exception, match="Server error"):
            engine.transcribe(_fake_pcm(), "en")


class TestGoogleSTT:
    def test_implements_protocol(self):
        from llm_code.voice.stt import STTEngine
        engine = GoogleSTT(language_code="en-US")
        assert isinstance(engine, STTEngine)

    @patch("llm_code.tools.voice._get_google_client")
    def test_transcribe_success(self, mock_get_client):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_result = MagicMock()
        mock_result.alternatives = [MagicMock(transcript="hello")]
        mock_response.results = [mock_result]
        mock_client.recognize.return_value = mock_response
        mock_get_client.return_value = mock_client

        mock_speech = MagicMock()
        mock_speech.RecognitionAudio = MagicMock(return_value=MagicMock())
        mock_speech.RecognitionConfig = MagicMock(return_value=MagicMock())
        mock_speech.RecognitionConfig.AudioEncoding = MagicMock(LINEAR16=MagicMock())

        with patch.dict("sys.modules", {"google": MagicMock(), "google.cloud": MagicMock(), "google.cloud.speech": mock_speech}):
            engine = GoogleSTT(language_code="en-US")
            result = engine.transcribe(_fake_pcm(), "en")
        assert result == "hello"

    @patch("llm_code.tools.voice._get_google_client")
    def test_transcribe_empty_result(self, mock_get_client):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.results = []
        mock_client.recognize.return_value = mock_response
        mock_get_client.return_value = mock_client

        mock_speech = MagicMock()
        mock_speech.RecognitionAudio = MagicMock(return_value=MagicMock())
        mock_speech.RecognitionConfig = MagicMock(return_value=MagicMock())

        with patch.dict("sys.modules", {"google": MagicMock(), "google.cloud": MagicMock(), "google.cloud.speech": mock_speech}):
            engine = GoogleSTT(language_code="en-US")
            result = engine.transcribe(_fake_pcm(), "en")
        assert result == ""


class TestAnthropicSTT:
    def test_implements_protocol(self):
        from llm_code.voice.stt import STTEngine
        engine = AnthropicSTT(ws_url="wss://api.anthropic.com")
        assert isinstance(engine, STTEngine)

    @patch("llm_code.tools.voice._ws_transcribe")
    def test_transcribe_success(self, mock_ws):
        mock_ws.return_value = "hello from anthropic"
        engine = AnthropicSTT(ws_url="wss://api.anthropic.com")
        result = engine.transcribe(_fake_pcm(), "en")
        assert result == "hello from anthropic"


class TestLocalWhisperSTT:
    """Local embedded Whisper via faster-whisper — no HTTP server."""

    def test_implements_protocol(self):
        from llm_code.tools.voice import LocalWhisperSTT
        from llm_code.voice.stt import STTEngine

        engine = LocalWhisperSTT()
        assert isinstance(engine, STTEngine)

    def test_lazy_model_loading(self):
        """Constructor must not load the model — importing faster-
        whisper and downloading weights would make `/voice on` block
        for several seconds on first use."""
        from llm_code.tools.voice import LocalWhisperSTT

        engine = LocalWhisperSTT(model_size="base")
        assert engine._model is None

    def test_missing_faster_whisper_raises_clear_error(self):
        """If faster-whisper isn't installed, transcribe must surface
        a runtime error that tells the user exactly which extras to
        install."""
        from llm_code.tools.voice import LocalWhisperSTT

        engine = LocalWhisperSTT()
        # Force the ImportError path by temporarily hiding the module.
        with patch.dict("sys.modules", {"faster_whisper": None}):
            with pytest.raises(RuntimeError, match="voice-local"):
                engine.transcribe(_fake_pcm(), "en")

    def test_transcribe_uses_mocked_model(self, tmp_path, monkeypatch):
        """Round-trip test with a mock WhisperModel — verifies the
        PCM→WAV→tempfile→segments pipeline without downloading a real
        model."""
        import sys

        from llm_code.tools.voice import LocalWhisperSTT

        mock_segment = MagicMock()
        mock_segment.text = "hello from local whisper"

        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([mock_segment], MagicMock())

        mock_wm = MagicMock(return_value=mock_model)
        mock_module = MagicMock()
        mock_module.WhisperModel = mock_wm

        monkeypatch.setitem(sys.modules, "faster_whisper", mock_module)

        engine = LocalWhisperSTT(model_size="tiny")
        result = engine.transcribe(_fake_pcm(), "en")

        assert result == "hello from local whisper"
        mock_wm.assert_called_once()
        call_args = mock_wm.call_args
        # model_size forwarded; device/compute_type defaulted
        assert call_args.args[0] == "tiny" or call_args.kwargs.get("model_size_or_path") == "tiny"


class TestCreateSTTEngineFactory:
    def test_factory_returns_local_when_backend_is_local(self):
        from llm_code.runtime.config_features import VoiceConfig
        from llm_code.tools.voice import LocalWhisperSTT, create_stt_engine

        cfg = VoiceConfig(backend="local", local_model="small")
        engine = create_stt_engine(cfg)
        assert isinstance(engine, LocalWhisperSTT)
        assert engine._model_size == "small"

    def test_factory_rejects_unknown_backend(self):
        from llm_code.runtime.config_features import VoiceConfig
        from llm_code.tools.voice import create_stt_engine

        cfg = VoiceConfig(backend="mystery")
        with pytest.raises(ValueError, match="Unknown STT backend"):
            create_stt_engine(cfg)
