"""Backward-compatibility shim.

``HidaEngine`` lives at :mod:`llm_code.runtime.hida` now. Re-exported here
so ``from llm_code.hida.engine import HidaEngine`` keeps working after the
Phase 5.5 merge.
"""
from llm_code.runtime.hida import HidaEngine  # noqa: F401

__all__ = ["HidaEngine"]
