"""Abstract base classes for tools."""
from __future__ import annotations

import dataclasses
import pathlib
from abc import ABC, abstractmethod
from enum import Enum
from typing import Callable

from pydantic import BaseModel

from llm_code.api.types import ToolDefinition


def resolve_path(raw: str) -> pathlib.Path:
    """Resolve a tool path argument to an existing file/directory.

    Handles the common LLM mistake of constructing wrong absolute paths
    (e.g. confusing directory name ``llm-code`` with package ``llm_code``).

    Resolution order:
    1. If the literal path exists, return it.
    2. If it's absolute and doesn't exist, try suffix matches under cwd.
    3. Otherwise return the original Path (caller decides how to handle).

    Security: resolved paths must be under cwd to prevent workspace escape.
    """
    p = pathlib.Path(raw)
    if p.exists():
        return p
    # Absolute path that doesn't exist — try stripping the prefix and
    # interpreting the tail relative to cwd.
    if p.is_absolute():
        cwd = pathlib.Path.cwd().resolve()
        # Walk each suffix of the path parts to find the longest match under cwd.
        parts = p.parts
        for i in range(1, len(parts)):
            candidate = cwd / pathlib.Path(*parts[i:])
            if candidate.exists():
                # Security: ensure resolved path is under cwd
                try:
                    candidate.resolve().relative_to(cwd)
                except ValueError:
                    continue  # skip — would escape workspace
                return candidate
    return p  # return as-is; caller will report file-not-found


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
