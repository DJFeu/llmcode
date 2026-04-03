"""Whisper STT backend — POST to OpenAI-compatible /v1/audio/transcriptions."""
from __future__ import annotations

import struct

import httpx


class WhisperSTT:
    """Transcribe audio via an OpenAI-compatible Whisper endpoint."""

    def __init__(self, url: str = "http://localhost:8000/v1/audio/transcriptions"):
        self._url = url

    def transcribe(self, audio_bytes: bytes, language: str) -> str:
        """Send PCM audio as WAV to the Whisper endpoint."""
        wav_data = _pcm_to_wav(audio_bytes)
        response = httpx.post(
            self._url,
            files={"file": ("audio.wav", wav_data, "audio/wav")},
            data={"language": language, "response_format": "json"},
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json().get("text", "")


def _pcm_to_wav(
    pcm: bytes,
    sample_rate: int = 16000,
    channels: int = 1,
    sample_width: int = 2,
) -> bytes:
    """Wrap raw PCM bytes in a WAV header."""
    data_size = len(pcm)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,  # PCM
        channels,
        sample_rate,
        sample_rate * channels * sample_width,
        channels * sample_width,
        sample_width * 8,
        b"data",
        data_size,
    )
    return header + pcm
