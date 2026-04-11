"""Voice input (STT) — backward-compatibility re-exports.

The canonical module is now ``llm_code.tools.voice`` (Phase 5.3 of the
2026-04-11 architecture refactor). This package survives only as a thin
re-export layer so existing imports like ``from llm_code.voice.recorder
import AudioRecorder`` keep working.
"""
from llm_code.tools.voice import (  # noqa: F401
    LANGUAGE_MAP,
    AnthropicSTT,
    AudioRecorder,
    GoogleSTT,
    RecorderBackend,
    STTEngine,
    WhisperSTT,
    create_stt_engine,
    detect_backend,
    validate_language,
)

__all__ = [
    "LANGUAGE_MAP",
    "AnthropicSTT",
    "AudioRecorder",
    "GoogleSTT",
    "RecorderBackend",
    "STTEngine",
    "WhisperSTT",
    "create_stt_engine",
    "detect_backend",
    "validate_language",
]
