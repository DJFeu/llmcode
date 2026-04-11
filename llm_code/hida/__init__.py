"""HIDA — backward-compatibility re-exports.

The canonical module is now ``llm_code.runtime.hida`` (Phase 5.5 of the
2026-04-11 architecture refactor). This package survives only as a thin
re-export layer so existing imports like ``from llm_code.hida.classifier
import TaskClassifier`` keep working.
"""
from llm_code.runtime.hida import (  # noqa: F401
    DEFAULT_PROFILES,
    HidaEngine,
    TaskClassifier,
    TaskProfile,
    TaskType,
)

__all__ = [
    "DEFAULT_PROFILES",
    "HidaEngine",
    "TaskClassifier",
    "TaskProfile",
    "TaskType",
]
