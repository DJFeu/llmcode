"""Frozen dataclass types for the LLM provider API layer."""
from __future__ import annotations

import dataclasses
from typing import Union


@dataclasses.dataclass(frozen=True)
class TextBlock:
    text: str


@dataclasses.dataclass(frozen=True)
class ToolUseBlock:
    id: str
    name: str
    input: dict


@dataclasses.dataclass(frozen=True)
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False


@dataclasses.dataclass(frozen=True)
class ImageBlock:
    media_type: str
    data: str


ContentBlock = Union[TextBlock, ToolUseBlock, ToolResultBlock, ImageBlock]


@dataclasses.dataclass(frozen=True)
class Message:
    role: str
    content: tuple[ContentBlock, ...]


@dataclasses.dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict


@dataclasses.dataclass(frozen=True)
class MessageRequest:
    model: str
    messages: tuple[Message, ...]
    system: str | None = None
    tools: tuple[ToolDefinition, ...] = ()
    max_tokens: int = 4096
    temperature: float = 0.7
    stream: bool = True
    extra_body: dict | None = None
    cache_key: str = ""
    metadata: dict | None = None


@dataclasses.dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int


@dataclasses.dataclass(frozen=True)
class MessageResponse:
    content: tuple[ContentBlock, ...]
    usage: TokenUsage
    stop_reason: str


@dataclasses.dataclass(frozen=True)
class StreamEvent:
    """Base class for all stream events."""


@dataclasses.dataclass(frozen=True)
class StreamMessageStart(StreamEvent):
    model: str


@dataclasses.dataclass(frozen=True)
class StreamTextDelta(StreamEvent):
    text: str


@dataclasses.dataclass(frozen=True)
class StreamToolUseStart(StreamEvent):
    id: str
    name: str


@dataclasses.dataclass(frozen=True)
class StreamToolUseInputDelta(StreamEvent):
    id: str
    partial_json: str


@dataclasses.dataclass(frozen=True)
class StreamMessageStop(StreamEvent):
    usage: TokenUsage
    stop_reason: str


@dataclasses.dataclass(frozen=True)
class StreamToolProgress(StreamEvent):
    tool_name: str
    message: str
    percent: float | None = None


@dataclasses.dataclass(frozen=True)
class StreamToolExecStart(StreamEvent):
    """Emitted when a tool starts executing."""
    tool_name: str
    args_summary: str
    tool_id: str = ""  # correlation key for matching with StreamToolExecResult


@dataclasses.dataclass(frozen=True)
class StreamToolExecResult(StreamEvent):
    """Emitted when a tool finishes executing."""
    tool_name: str
    output: str
    is_error: bool = False
    metadata: dict | None = None
    tool_id: str = ""  # correlation key matching the StreamToolExecStart that opened it


@dataclasses.dataclass(frozen=True)
class StreamThinkingDelta(StreamEvent):
    """Emitted when the model produces a thinking/reasoning token."""
    text: str


@dataclasses.dataclass(frozen=True)
class StreamCompactionStart(StreamEvent):
    """Emitted when auto-compaction starts in the background."""
    used_tokens: int
    max_tokens: int


@dataclasses.dataclass(frozen=True)
class StreamCompactionDone(StreamEvent):
    """Emitted when auto-compaction finishes."""
    before_messages: int
    after_messages: int


@dataclasses.dataclass(frozen=True)
class StreamPermissionRequest(StreamEvent):
    """Emitted when a tool requires user permission before execution."""
    tool_name: str
    args_preview: str
