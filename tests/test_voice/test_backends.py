"""Tests for STT backend implementations."""
from __future__ import annotations

import json
import struct
from unittest.mock import AsyncMock, MagicMock, patch

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

    @patch("llm_code.voice.stt_google._get_client")
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

    @patch("llm_code.voice.stt_google._get_client")
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

    @patch("llm_code.voice.stt_anthropic._ws_transcribe")
    def test_transcribe_success(self, mock_ws):
        mock_ws.return_value = "hello from anthropic"
        engine = AnthropicSTT(ws_url="wss://api.anthropic.com")
        result = engine.transcribe(_fake_pcm(), "en")
        assert result == "hello from anthropic"
