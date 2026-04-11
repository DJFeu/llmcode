"""Backward-compatibility shim — Google STT lives at llm_code.tools.voice.

Re-exports ``_get_client`` under its legacy name so tests that patch
``llm_code.voice.stt_google._get_client`` keep working.
"""
from llm_code.tools.voice import GoogleSTT, _get_google_client  # noqa: F401

# Legacy alias for pre-refactor tests.
_get_client = _get_google_client

__all__ = ["GoogleSTT"]
