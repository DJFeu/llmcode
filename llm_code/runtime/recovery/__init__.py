"""Runtime recovery helpers.

Pure functions that take a suspicious / broken assistant message (or
session fragment) and return a repaired version. Each sub-module
targets one recovery mode from the Wave2-1 session-recovery plan.
"""
from llm_code.runtime.recovery.thinking_order import (
    ThinkingOrderRepair,
    repair_assistant_content_order,
)

__all__ = [
    "ThinkingOrderRepair",
    "repair_assistant_content_order",
]
