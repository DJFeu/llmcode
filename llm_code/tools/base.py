"""Abstract base classes for tools."""
from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from enum import Enum

from llm_code.api.types import ToolDefinition


class PermissionLevel(Enum):
    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    FULL_ACCESS = "full_access"


@dataclasses.dataclass(frozen=True)
class ToolResult:
    output: str
    is_error: bool = False
    metadata: dict | None = None


class Tool(ABC):
    """Abstract base class for all tools."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def input_schema(self) -> dict: ...

    @property
    @abstractmethod
    def required_permission(self) -> PermissionLevel: ...

    @abstractmethod
    def execute(self, args: dict) -> ToolResult: ...

    def to_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
        )
