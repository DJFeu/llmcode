"""Frozen dataclasses and enums for HIDA task classification."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TaskType(Enum):
    CODING = "coding"
    DEBUGGING = "debugging"
    REVIEWING = "reviewing"
    PLANNING = "planning"
    TESTING = "testing"
    REFACTORING = "refactoring"
    RESEARCH = "research"
    DEPLOYMENT = "deployment"
    DOCUMENTATION = "documentation"
    CONVERSATION = "conversation"


@dataclass(frozen=True)
class TaskProfile:
    task_type: TaskType
    confidence: float
    tools: frozenset[str]
    memory_keys: frozenset[str]
    governance_categories: frozenset[str]
    load_full_prompt: bool
