"""Memory tools: store, recall, and list cross-session memory entries."""
from __future__ import annotations

from pydantic import BaseModel

from llm_code.runtime.memory import MemoryStore
from llm_code.tools.base import PermissionLevel, Tool, ToolResult


class MemoryStoreInput(BaseModel):
    key: str
    value: str


class MemoryRecallInput(BaseModel):
    key: str


class MemoryStoreTool(Tool):
    """Store a value in persistent memory under a given key."""

    def __init__(self, memory: MemoryStore) -> None:
        self._memory = memory

    @property
    def name(self) -> str:
        return "memory_store"

    @property
    def description(self) -> str:
        return "Store a value in persistent cross-session memory under a given key."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "The memory key"},
                "value": {"type": "string", "description": "The value to store"},
            },
            "required": ["key", "value"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.WORKSPACE_WRITE

    @property
    def input_model(self) -> type[MemoryStoreInput]:
        return MemoryStoreInput

    def execute(self, args: dict) -> ToolResult:
        self._memory.store(args["key"], args["value"])
        return ToolResult(output=f"Stored: {args['key']}")


class MemoryRecallTool(Tool):
    """Recall a value from persistent memory by key."""

    def __init__(self, memory: MemoryStore) -> None:
        self._memory = memory

    @property
    def name(self) -> str:
        return "memory_recall"

    @property
    def description(self) -> str:
        return "Recall a value from persistent cross-session memory by key."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "The memory key to recall"},
            },
            "required": ["key"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    @property
    def input_model(self) -> type[MemoryRecallInput]:
        return MemoryRecallInput

    def is_read_only(self, args: dict) -> bool:
        return True

    def is_concurrency_safe(self, args: dict) -> bool:
        return True

    def execute(self, args: dict) -> ToolResult:
        value = self._memory.recall(args["key"])
        if value is None:
            return ToolResult(output=f"No memory found for key: {args['key']}", is_error=True)
        return ToolResult(output=value)


class MemoryListTool(Tool):
    """List all keys and values stored in persistent memory."""

    def __init__(self, memory: MemoryStore) -> None:
        self._memory = memory

    @property
    def name(self) -> str:
        return "memory_list"

    @property
    def description(self) -> str:
        return "List all keys and values stored in persistent cross-session memory."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {},
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    def is_read_only(self, args: dict) -> bool:
        return True

    def execute(self, args: dict) -> ToolResult:
        entries = self._memory.get_all()
        if not entries:
            return ToolResult(output="No memories stored.")
        lines = [
            f"- {k}: {v.value[:50]}..." if len(v.value) > 50 else f"- {k}: {v.value}"
            for k, v in entries.items()
        ]
        return ToolResult(output="\n".join(lines))
