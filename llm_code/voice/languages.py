"""Backward-compatibility shim — language table lives at llm_code.tools.voice."""
from llm_code.tools.voice import LANGUAGE_MAP, validate_language  # noqa: F401

__all__ = ["LANGUAGE_MAP", "validate_language"]
