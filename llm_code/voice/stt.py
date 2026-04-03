"""STT engine protocol and factory."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from llm_code.runtime.config import VoiceConfig


@runtime_checkable
class STTEngine(Protocol):
    """Protocol for speech-to-text backends."""

    def transcribe(self, audio_bytes: bytes, language: str) -> str:
        """Transcribe raw PCM audio bytes to text."""
        ...


def create_stt_engine(config: VoiceConfig) -> STTEngine:
    """Factory: create an STT engine from config."""
    backend = config.backend

    if backend == "whisper":
        from llm_code.voice.stt_whisper import WhisperSTT
        return WhisperSTT(url=config.whisper_url)

    if backend == "google":
        from llm_code.voice.stt_google import GoogleSTT
        return GoogleSTT(language_code=config.google_language_code or config.language)

    if backend == "anthropic":
        from llm_code.voice.stt_anthropic import AnthropicSTT
        return AnthropicSTT(ws_url=config.anthropic_ws_url)

    raise ValueError(
        f"Unknown STT backend: {backend!r}. Valid: whisper, google, anthropic"
    )
