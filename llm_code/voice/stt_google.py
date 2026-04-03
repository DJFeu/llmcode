"""Google Cloud Speech STT backend."""
from __future__ import annotations


def _get_client():
    """Lazy import and create Google Speech client."""
    from google.cloud import speech  # type: ignore[import]
    return speech.SpeechClient()


class GoogleSTT:
    """Transcribe audio via Google Cloud Speech-to-Text API."""

    def __init__(self, language_code: str = "en-US"):
        self._language_code = language_code

    def transcribe(self, audio_bytes: bytes, language: str) -> str:
        """Send PCM audio to Google Cloud Speech."""
        from google.cloud import speech  # type: ignore[import]

        client = _get_client()
        audio = speech.RecognitionAudio(content=audio_bytes)
        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=16000,
            language_code=self._language_code if self._language_code else language,
        )
        response = client.recognize(config=config, audio=audio)

        if not response.results:
            return ""
        return response.results[0].alternatives[0].transcript
