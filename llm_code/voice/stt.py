"""Backward-compatibility shim.

The STT protocol and factory live at :mod:`llm_code.tools.voice` now.
Re-exported here so ``from llm_code.voice.stt import create_stt_engine``
keeps working after the Phase 5.3 merge.
"""
from llm_code.tools.voice import STTEngine, create_stt_engine  # noqa: F401

__all__ = ["STTEngine", "create_stt_engine"]
