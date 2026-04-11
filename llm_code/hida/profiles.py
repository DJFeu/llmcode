"""Backward-compatibility shim.

HIDA default profiles live at :mod:`llm_code.runtime.hida` now.
Re-exported here so ``from llm_code.hida.profiles import DEFAULT_PROFILES``
keeps working after the Phase 5.5 merge.
"""
from llm_code.runtime.hida import DEFAULT_PROFILES  # noqa: F401

__all__ = ["DEFAULT_PROFILES"]
