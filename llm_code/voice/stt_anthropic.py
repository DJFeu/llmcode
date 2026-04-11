"""Backward-compatibility shim — Anthropic STT lives at llm_code.tools.voice.

Re-exports ``_ws_transcribe`` so tests that patch
``llm_code.voice.stt_anthropic._ws_transcribe`` keep working.
"""
from llm_code.tools.voice import AnthropicSTT, _ws_transcribe  # noqa: F401

__all__ = ["AnthropicSTT"]
