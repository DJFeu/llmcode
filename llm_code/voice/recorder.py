"""Backward-compatibility shim.

Audio recording lives at :mod:`llm_code.tools.voice` now. Re-exported
here so ``from llm_code.voice.recorder import AudioRecorder`` keeps
working after the Phase 5.3 merge.
"""
from llm_code.tools.voice import (  # noqa: F401
    AudioRecorder,
    RecorderBackend,
    detect_backend,
)

__all__ = ["AudioRecorder", "RecorderBackend", "detect_backend"]
