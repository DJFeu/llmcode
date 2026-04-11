"""Backward-compatibility shim — Whisper STT lives at llm_code.tools.voice."""
from llm_code.tools.voice import WhisperSTT  # noqa: F401

__all__ = ["WhisperSTT"]
