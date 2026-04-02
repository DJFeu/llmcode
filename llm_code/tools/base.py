"""Abstract base classes for tools."""
from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from enum import Enum
from typing import Callable

from pydantic import BaseModel

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


@dataclasses.dataclass(frozen=True)
class ToolProgress:
    tool_name: str
    message: str
    percent: float | None = None


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

    @property
    def input_model(self) -> type[BaseModel] | None:
        """Return the Pydantic model class for input validation, or None."""
        return None

    def is_read_only(self, args: dict) -> bool:
        """Return True if this operation only reads data (never writes)."""
        return False

    def is_destructive(self, args: dict) -> bool:
        """Return True if this operation can cause irreversible data loss."""
        return False

    def is_concurrency_safe(self, args: dict) -> bool:
        """Return True if this operation is safe to run concurrently."""
        return False

    def validate_input(self, args: dict) -> dict:
        """Validate args against input_model; return coerced dict or raise ValidationError."""
        model_cls = self.input_model
        if model_cls is None:
            return args
        validated = model_cls(**args)
        return validated.model_dump()

    def execute_with_progress(
        self,
        args: dict,
        on_progress: Callable[[ToolProgress], None],
    ) -> ToolResult:
        """Execute the tool, optionally emitting progress events via on_progress."""
        return self.execute(args)

    def to_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
        )
