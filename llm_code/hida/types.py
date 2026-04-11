"""Backward-compatibility shim.

HIDA types live at :mod:`llm_code.runtime.hida` now. Re-exported here so
``from llm_code.hida.types import TaskProfile, TaskType`` keeps working
after the Phase 5.5 merge.
"""
from llm_code.runtime.hida import TaskProfile, TaskType  # noqa: F401

__all__ = ["TaskProfile", "TaskType"]
