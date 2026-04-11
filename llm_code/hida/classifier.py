"""Backward-compatibility shim.

``TaskClassifier`` lives at :mod:`llm_code.runtime.hida` now. Re-exported
here so ``from llm_code.hida.classifier import TaskClassifier`` keeps
working after the Phase 5.5 merge.
"""
from llm_code.runtime.hida import TaskClassifier  # noqa: F401

__all__ = ["TaskClassifier"]
